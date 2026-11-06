# -*- coding: utf-8 -*-
"""模型层：弹性分档、组合回归、逐日归因、盘中反推。

全部输入来自历史库（store），不写死任何日期或数字；换窗口、加标的、加天数都只改数据。
"""
import statistics

MIN_DAYS = 8          # 少于 8 个交易日回归没有意义
TIER_B = 0.8          # 一档门槛：成交额-净申购弹性
TIER_R2 = 0.7         # 一档门槛：拟合度
W_MIN, W_MAX = 0.35, 0.75   # 归因系数区间
W_KNEE = 3.6667       # 成交额到常态的 W_KNEE 倍时 w 打满（与 0.15/倍 的斜率等价）


def linreg(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    b = sxy / sxx if sxx else 0.0
    a = my - b * mx
    tot = sum((v - my) ** 2 for v in y)
    res = sum((v - (a + b * u)) ** 2 for u, v in zip(x, y))
    return a, b, (1 - res / tot if tot else 0.0)


def med(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def wof(x):
    """放量越极端，国家队在净申购里占比越高。x = 当日成交额 / 常态成交额。"""
    return min(W_MAX, max(W_MIN, W_MIN + (W_MAX - W_MIN) * ((x - 1) / (W_KNEE - 1))))


def _trend_spans(rows, HS, band=0.015, min_days=3):
    """按基准的波段把交易日切段：从起点跟踪极值，回撤超过 band 就在极值处换向。

    这样切出来的是真正的「一波下跌 / 一波反弹」，不是按固定天数硬切。
    """
    n = len(rows)
    h = [HS.get(r["date"]) for r in rows]
    if n < 4:
        return [[0, n]]
    spans, i = [], 0
    while i < n - 1:
        d = 1 if (h[i + 1] or 0) >= (h[i] or 0) else -1
        ext, ext_i = h[i], i
        j = i + 1
        while j < n:
            x = h[j]
            if x is None:
                j += 1
                continue
            if d > 0:
                if x > ext:
                    ext, ext_i = x, j
                if x <= ext * (1 - band):
                    break
            else:
                if x < ext:
                    ext, ext_i = x, j
                if x >= ext * (1 + band):
                    break
            j += 1
        end = max(ext_i, i + 1)
        spans.append([i, end])
        i = end
    if spans[-1][1] < n:
        spans[-1][1] = n
    merged = []
    for sp in spans:
        if merged and sp[1] - sp[0] < min_days:
            merged[-1][1] = sp[1]
        else:
            merged.append(sp)
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_days:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    return merged


def _r(x, n=1):
    return round(x, n)


def compute(universe, daily, intraday, window=21, seg=5):
    etfs = universe["etfs"]
    codes = [e["code"] for e in etfs]
    meta = {e["code"]: e for e in etfs}

    all_dates = [d for d in sorted(daily.get("benchmark", {}).get("close", {}))]
    usable = None
    for c in codes:
        rec = daily["etfs"].get(c) or {}
        s = set(rec.get("turnover", {})) & set(rec.get("flow", {}))
        usable = s if usable is None else (usable & s)
    usable = sorted(usable or [])
    if len(usable) < MIN_DAYS:
        raise SystemExit(
            f"可用交易日只有 {len(usable)} 天，至少要 {MIN_DAYS} 天才能回归。先跑 update.py plan 补数据。")

    dates = usable[-window:] if window else usable
    N = len(dates)
    HS = daily["benchmark"]["close"]

    def turn(c):
        r = daily["etfs"][c]["turnover"]
        return [r[d] / 1e8 for d in dates]        # 亿元

    def flow(c):
        r = daily["etfs"][c]["flow"]
        return [r[d] for d in dates]

    # -------------------------------------------------- 逐只诊断 + 自动分档
    diag, V0 = [], {}
    for c in codes:
        V, F = turn(c), flow(c)
        a, b, r2 = linreg(V, F)
        m = med(V)
        v0 = -a / b if b > 0.05 else 0.0
        fb = False
        if v0 <= 0 or v0 > 1.5 * m:      # 低弹性标的截距不稳，用中位数兜底
            v0, fb = m, True
        V0[c] = v0
        diag.append(dict(
            code=c, name=meta[c]["name"], idx=meta[c].get("index", ""),
            group=meta[c].get("group", ""), scale=_r(meta[c].get("scale", 0), 0),
            b=_r(b, 3), r2=_r(r2, 3), v0=_r(v0, 1), v0_fallback=fb,
            flow=_r(sum(F), 1), turn=_r(sum(V), 1),
            k=_r(sum(F) / sum(V), 3) if sum(V) else 0,
        ))
    tier1 = [d["code"] for d in diag if d["b"] >= TIER_B and d["r2"] >= TIER_R2]
    if not tier1:                                   # 极端情况兜底：取弹性最高的两只
        tier1 = [d["code"] for d in sorted(diag, key=lambda z: -z["b"])[:2]]
    tier2 = [d["code"] for d in diag if d["code"] not in tier1]
    for d in diag:
        d["tier"] = 1 if d["code"] in tier1 else 2
    diag.sort(key=lambda d: (-d["b"], -d["flow"]))

    # -------------------------------------------------- 组合回归
    PV = [sum(daily["etfs"][c]["turnover"][d] for c in tier1) / 1e8 for d in dates]
    PN = [sum(daily["etfs"][c]["flow"][d] for c in tier1) for d in dates]
    pa, pb, pr2 = linreg(PV, PN)
    pV0 = -pa / pb if pb > 0.05 else med(PV)
    if pV0 <= 0 or pV0 > 1.5 * med(PV):
        pV0 = med(PV)

    PV2 = [sum(daily["etfs"][c]["turnover"][d] for c in tier2) / 1e8 for d in dates] if tier2 else [0.0] * N
    PN2 = [sum(daily["etfs"][c]["flow"][d] for c in tier2) for d in dates] if tier2 else [0.0] * N
    if tier2:
        qa, qb, qr2 = linreg(PV2, PN2)
        qV0 = med(PV2)          # 成交额与申购已脱钩，截距不可信
    else:
        qa, qb, qr2, qV0 = 0.0, 0.0, 0.0, 0.0

    PV_ALL = [sum(daily["etfs"][c]["turnover"][d] for c in codes) / 1e8 for d in dates]
    PN_ALL = [sum(daily["etfs"][c]["flow"][d] for c in codes) for d in dates]

    # -------------------------------------------------- 逐日归因
    rows, cum, cumser = [], 0.0, []
    for i, d in enumerate(dates):
        w1 = wof(PV[i] / pV0) if pV0 else W_MIN
        w2 = wof(PV2[i] / qV0) if qV0 else 0.0
        nat1, nat2 = PN[i] * w1, PN2[i] * w2
        nat = nat1 + nat2
        wn = abs(PN[i]) * w1 + abs(PN2[i]) * w2
        wd = abs(PN[i]) + abs(PN2[i])
        cum += nat
        cumser.append(_r(cum, 1))
        tops = sorted(((meta[c]["name"], _r(daily["etfs"][c]["flow"][d], 1)) for c in codes),
                      key=lambda x: -abs(x[1]))[:3]
        prev = HS.get(dates[i - 1]) if i else None
        hs = HS.get(d)
        rows.append(dict(
            date=d, hs=hs,
            dhs=_r((hs / prev - 1) * 100, 2) if (hs and prev) else 0.0,
            turn1=_r(PV[i], 1), flow1=_r(PN[i], 1), flow_all=_r(PN_ALL[i], 1),
            w=_r((wn / wd) if wd else 0, 2),
            nat=_r(nat, 1), nat1=_r(nat1, 1), nat2=_r(nat2, 1),
            x=_r(PV[i] / pV0, 2) if pV0 else 0.0,
            top=tops))

    buy = sum(r["nat"] for r in rows if r["nat"] > 0)
    sell = sum(r["nat"] for r in rows if r["nat"] < 0)
    peak = max(rows, key=lambda r: r["nat"])
    avg_w = sum(r["w"] for r in rows) / N

    # -------------------------------------------------- 逐只归因
    attr = []
    for c in codes:
        V, F = turn(c), flow(c)
        a, b, r2 = linreg(V, F)
        wser = [wof(PV[i] / pV0) if c in tier1 else (wof(PV2[i] / qV0) if qV0 else 0.0)
                for i in range(N)]
        nat_c = sum(F[i] * wser[i] for i in range(N))
        attr.append(dict(
            code=c, name=meta[c]["name"], idx=meta[c].get("index", ""),
            group=meta[c].get("group", ""), scale=_r(meta[c].get("scale", 0), 0),
            flow=_r(sum(F), 1), turn=_r(sum(V), 1),
            k=_r(sum(F) / sum(V), 3) if sum(V) else 0,
            nat=_r(nat_c, 1), b=_r(b, 3), r2=_r(r2, 3), v0=_r(V0[c], 1),
            tier=1 if c in tier1 else 2,
            w=_r(nat_c / sum(F), 2) if sum(F) else 0))
    attr.sort(key=lambda d: -d["nat"])

    def agg(cs):
        f = sum(sum(daily["etfs"][c]["flow"][d] for d in dates) for c in cs)
        t = sum(sum(daily["etfs"][c]["turnover"][d] for d in dates) for c in cs) / 1e8
        return dict(flow=_r(f, 1), turn=_r(t, 1),
                    scale=_r(sum(meta[c].get("scale", 0) for c in cs), 0),
                    k=_r(f / t, 3) if t else 0,
                    nat=_r(sum(x["nat"] for x in attr if x["code"] in cs), 1))
    t1, t2, allsum = agg(tier1), agg(tier2), agg(codes)

    # -------------------------------------------------- 阶段拆分：按基准走势自动切段
    segments = []
    for i, j in _trend_spans(rows, HS, min_days=max(2, seg)):
        blk = rows[i:j]
        h0, h1 = HS.get(blk[0]["date"]), HS.get(blk[-1]["date"])
        chg = ((h1 / h0 - 1) * 100) if (h0 and h1) else 0.0
        nat_sum = sum(b["nat"] for b in blk)
        tag = "下行" if chg <= -1 else ("上行" if chg >= 1 else "横盘")
        segments.append(dict(
            start=blk[0]["date"], end=blk[-1]["date"], days=len(blk),
            nat=_r(nat_sum, 1), chg=_r(chg, 2), tag=tag, sign=1 if chg > 0 else -1,
            label=f'{blk[0]["date"][5:].replace("-", "/")}–{blk[-1]["date"][5:].replace("-", "/")}'))

    # -------------------------------------------------- 收盘实盘（比窗口更新、当天只有成交额的一天）
    # 收盘后成交额当天就有、净申购往往要等晚上才更新，这一段让看板不必空等到深夜：
    # 一档用回归方程直接从全天成交额反推，二档没有可用的反推关系，只报成交额倍数。
    close = None
    _tier2_turn_hist = [sum(daily["etfs"][c]["turnover"][d] for c in tier2) / 1e8
                        for d in dates] if tier2 else [0.0] * N
    for d in reversed([x for x in all_dates if x > dates[-1]]):
        if not all(d in (daily["etfs"].get(c) or {}).get("turnover", {}) for c in tier1):
            continue
        t1d = sum(daily["etfs"][c]["turnover"][d] for c in tier1) / 1e8
        t2d = sum(daily["etfs"][c]["turnover"][d] for c in tier2) / 1e8 if tier2 else 0.0
        tall = t1d + t2d
        ref = PV[max(0, N - 5):] or PV
        base5 = sum(ref) / len(ref)
        x5 = t1d / base5 if base5 else 0.0
        w = wof(x5) if pV0 else W_MIN
        pf = pa + pb * t1d
        ref2 = _tier2_turn_hist[max(0, N - 5):] or _tier2_turn_hist
        base5b = sum(ref2) / len(ref2) if ref2 else 0.0
        hs, hsp = HS.get(d), HS.get(dates[-1])
        per = []
        for c in codes:
            tv = (daily["etfs"].get(c) or {}).get("turnover", {}).get(d)
            if not tv:
                continue
            v = tv / 1e8
            per.append(dict(code=c, name=meta[c]["name"], group=meta[c].get("group", ""),
                            turn=_r(v, 2), v0=_r(V0[c], 1),
                            x=_r(v / V0[c], 2) if V0[c] else 0,
                            tier=1 if c in tier1 else 2))
        per.sort(key=lambda z: -z["x"])
        close = dict(
            date=d, turn1=_r(t1d, 1), turn2=_r(t2d, 1), turn_all=_r(tall, 1),
            base5=_r(base5, 1), x5=_r(x5, 2),
            x_norm=_r(t1d / pV0, 2) if pV0 else 0,
            tier2_x=_r(t2d / base5b, 2) if base5b else 0,
            hs=hs, hs_chg=_r((hs / hsp - 1) * 100, 2) if (hs and hsp) else 0.0,
            pred_flow=_r(pf, 1), w=_r(w, 2), pred_nat=_r(pf * w, 1),
            threshold=_r(2 * pV0, 0), entered=bool(t1d >= 2 * pV0),
            per_etf=per)
        break

    # -------------------------------------------------- 盘中反推
    live = None
    half = (intraday or {}).get("half") or {}
    if half:
        picked = {c: float(half[c]) for c in tier1 if c in half}
        if picked:
            half_sum = sum(picked.values())
            ratio = float((intraday or {}).get("ratio") or 0.55)
            full = half_sum / ratio
            ref = [PV[i] for i in range(max(0, N - 5), N)] or PV
            base5 = (sum(ref) / len(ref)) * ratio
            live = dict(
                date=intraday.get("date"), time=intraday.get("time", ""),
                half=_r(half_sum, 1), ratio=ratio, full=_r(full, 1),
                half5=_r(base5, 1), x5=_r(half_sum / base5, 2) if base5 else 0,
                hs=intraday.get("hs"), hs_chg=intraday.get("hs_chg"),
                per_etf=sorted(
                    (dict(code=c, name=meta[c]["name"], half=_r(v, 2),
                          full=_r(v / ratio, 1), v0=_r(V0[c], 1),
                          x=_r((v / ratio) / V0[c], 2) if V0[c] else 0) for c, v in picked.items()),
                    key=lambda z: -z["x"]),
                pred_flow=_r(pa + pb * full, 1),
                pred_nat=_r((pa + pb * full) * 0.45, 1),
                threshold=_r(2 * pV0, 0), entered=(half_sum / base5 >= 2) if base5 else False)
        else:
            live = dict(stale=True, date=intraday.get("date"), time=intraday.get("time", ""),
                        half=None)

    return dict(
        schema=1,
        window=dict(days=N, start=dates[0], end=dates[-1], seg=max(2, seg),
                    available=len(usable), total_available=len(all_dates)),
        diag=diag, rows=rows, cumser=cumser, attr=attr,
        tier1=tier1, tier2=tier2,
        pool=dict(a=_r(pa, 1), b=_r(pb, 3), r2=_r(pr2, 3), v0=_r(pV0, 1)),
        pool2=dict(a=_r(qa, 1), b=_r(qb, 3), r2=_r(qr2, 3), v0=_r(qV0, 1)),
        t1=t1, t2=t2, all=allsum,
        total=dict(buy=_r(buy, 1), sell=_r(sell, 1), net=_r(buy + sell, 1),
                   avg_w=_r(avg_w, 2), pos_days=sum(1 for r in rows if r["nat"] > 0),
                   peak=peak),
        segments=segments, live=live, close=close,
        universe=universe)
