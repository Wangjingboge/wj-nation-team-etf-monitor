# -*- coding: utf-8 -*-
"""渲染层：把 model 的结果写成单页 HTML 看板。

原则：所有数字都从数据算出来，正文里的「哪一天买了多少」也由程序生成，
不写死任何日期，这样换窗口 / 加天数后文案不会自相矛盾。
"""
import json

RED, GREEN = "#c8342c", "#12946a"


def _d(s):
    return s[5:].replace("-", "/") if s else ""


def _sgn(v, n=0):
    return f"{v:+,.{n}f}"


def _pearson(x, y):
    n = len(x)
    if n < 3:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sx = sum((a - mx) ** 2 for a in x) ** 0.5
    sy = sum((b - my) ** 2 for b in y) ** 0.5
    if not sx or not sy:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


# ----------------------------------------------------------------- 文案生成
def _rhythm_text(r):
    rows, segs = r["rows"], r["segments"]
    peak = r["total"]["peak"]
    net = r["total"]["net"] or 1
    corr = _pearson([x["dhs"] for x in rows], [x["nat"] for x in rows])

    down = sorted([x for x in rows if x["dhs"] < 0], key=lambda x: x["dhs"])[:3]
    up = sorted([x for x in rows if x["dhs"] > 0], key=lambda x: -x["dhs"])[:2]
    last, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)

    out = []
    if segs:
        parts = [f'<b>{s["label"]}</b> 沪深300 {_sgn(s["chg"], 2)}%，净买入 {s["nat"]:,.0f} 亿'
                 for s in segs]
        out.append(f'把 {r["window"]["days"]} 个交易日按沪深300的波段切开，一共 {len(segs)} 段：' +
                   "；".join(parts) + "。")
    if down:
        ds = "、".join(f'<b>{_d(x["date"])}</b>（{_sgn(x["dhs"], 2)}%）' for x in down)
        dn = sum(x["nat"] for x in down)
        out.append(f'跌得最狠的三天是 {ds}，合计买了 <b>{dn:,.0f} 亿</b>，占全期 {dn/net*100:.0f}%——'
                   f'跌幅和买入的相关系数 <b>{corr:+.2f}</b>，'
                   + ("跌得越狠买得越猛，这是国家队资金最典型的指纹。" if corr < -0.3
                      else "跌幅与买入只是弱相关，说明这一段的买盘不完全是「跌了才买」，还有配置盘在里面。"))
    if up:
        us = "、".join(f'<b>{_d(x["date"])}</b>（{_sgn(x["dhs"], 2)}%）' for x in up)
        un = sum(x["nat"] for x in up)
        out.append(f'反过来了看，涨幅最大的两天 {us} 买入 {un:,.0f} 亿，'
                   + ("明显小于下跌日，涨上去就收手。" if un < dn * 0.5 else "并没有明显收手。"))
    out.append(f'单日峰值 <b>{_d(peak["date"])}</b>，一天砸进 <b>{peak["nat"]:,.0f} 亿</b>，'
               f'当日一档成交额是常态的 {peak["x"]:.1f} 倍；'
               + (f'到 <b>{_d(last["date"])}</b> 沪深300 {_sgn(last["dhs"], 2)}%，'
                  f'净买入回落到 <b>{last["nat"]:+,.0f} 亿</b>。' if prev else ''))
    return "".join(out)


def _groups_text(r):
    uni = r["universe"]["etfs"]
    order, bucket = [], {}
    for e in uni:
        g = e.get("group") or "其他"
        if g not in bucket:
            bucket[g] = []
            order.append(g)
        bucket[g].append(e)
    bits = []
    for g in order:
        members = bucket[g]
        names = "、".join(f'{e.get("short") or e["name"].replace("ETF", "")} {e["code"]}' for e in members)
        bits.append(f'<b>{g} {len(members)} 只</b>——{names}')
    return "；".join(bits)


def _tier_text(r):
    d = {x["code"]: x for x in r["diag"]}
    t1, t2 = r["tier1"], r["tier2"]
    s1 = "、".join(d[c]["name"].replace("ETF", "") for c in t1)
    out1 = (f'<b>一档 {len(t1)} 只</b>（b {min(d[c]["b"] for c in t1):.2f}–{max(d[c]["b"] for c in t1):.2f}、'
            f'R² {min(d[c]["r2"] for c in t1):.2f}–{max(d[c]["r2"] for c in t1):.2f}）：{s1}，'
            f'合计规模 <b>{r["t1"]["scale"]:,.0f} 亿</b>。这一档成交额和申购几乎同步，'
            f'<b>盘中放量能直接读成国家队动手</b>。')
    if t2:
        s2 = "、".join(f'{d[c]["name"].replace("ETF", "")}（{d[c]["b"]:.2f}/{d[c]["r2"]:.2f}）' for c in
                       sorted(t2, key=lambda c: -d[c]["b"]))
        out2 = (f'<br><b>二档 {len(t2)} 只</b>：{s2}，合计规模 <b>{r["t2"]["scale"]:,.0f} 亿</b>、'
                f'这段时间净申购 <b>{r["t2"]["flow"]:,.0f} 亿</b>。它们同样是持仓，'
                f'只是成交额里二级博弈占比高、<b>不能反推</b>，只能等收盘用份额变动算。')
    else:
        out2 = ""
    return out1 + out2


def _quiet_text(r):
    """同指数多只产品时，自动指出「钱主要落在哪只」。"""
    uni = r["universe"]["etfs"]
    by_idx = {}
    for e in uni:
        by_idx.setdefault(e.get("index", ""), []).append(e["code"])
    flows = {x["code"]: x["flow"] for x in r["attr"]}
    bits = []
    for idx, cs in by_idx.items():
        if len(cs) < 2:
            continue
        big = max(cs, key=lambda c: flows[c])
        small = min(cs, key=lambda c: flows[c])
        if flows[big] <= 0 or flows[small] > 0.25 * flows[big]:
            continue
        nm = {e["code"]: e for e in uni}
        bits.append(f'<b>{idx}</b> 有 {len(cs)} 只产品，钱明显落在 '
                    f'{nm[big]["short"] or nm[big]["name"]}（净申购 {flows[big]:,.0f} 亿），'
                    f'{nm[small]["short"] or nm[small]["name"]} 只有 {flows[small]:,.0f} 亿')
    if not bits:
        return ""
    return ('<p class="p">名单里有件事值得留意：' + "；".join(bits) +
            '。指数一样、方向一样，最后钱落在哪只产品上，份额数字会告诉你。</p>')


def _caveat_text(r):
    a = r["all"]
    lo, hi = a["flow"] * 0.35, a["flow"] * 0.75
    t1, t2 = r["t1"], r["t2"]
    s2 = ""
    if r["tier2"]:
        s2 = (f'②二档 {len(r["tier2"])} 只的 w 更不可靠——它们的成交额与申购本就脱钩，'
              f'"放量推高国家队占比"这条经验在它们身上没有验证，所以二档那 {t2["nat"]:,.0f} 亿'
              f'误差明显大于一档，<b>引用时建议只当方向、不当金额</b>；')
    return (f'<b>硬的地方：</b>净申购额是 Wind ETF 日频资金净流入（份额变动 × 净值）的实测值，不是估算；'
            f'弹性 b 和 R² 是从 {r["window"]["days"]} 天真实数据回归出来的，'
            f'一档组合 R² 达 {r["pool"]["r2"]:.2f}，关系稳定。<br>'
            f'<b>软的地方：</b>①归因系数 w 是<b>假设</b>不是实测——净申购里国家队到底占几成没有公开数据能验证，'
            f'只能靠「放量越极端、占比越高」的经验设定；若整体按 0.35 或 0.75 取值，'
            f'这段总额就变成 <b>{lo:,.0f} 亿或 {hi:,.0f} 亿</b>，<b>区间比点值更可信</b>；'
            f'{s2}'
            f'③样本只有 {r["window"]["days"]} 个交易日，弹性在震荡市和单边市之间会漂移，建议每月重算；'
            f'④成交额→申购的映射在极端行情会失效——涨跌停、ETF 溢价过大时套利盘停摆，两者会脱钩；'
            f'⑤国家队也可能通过场外专户申购联接基金，那部分不体现在场内份额变动里，'
            f'<b>本模型估算的是下限</b>。<br>'
            f'<b>怎么用：</b>别看单日绝对数字，看三条——放量倍数（一档全天成交额 / {r["pool"]["v0"]:,.0f} 亿常态）、'
            f'申购转化率（净申购 / 成交额）、以及下跌日的买入是否放大。三条同时上升，才是国家队真的在托；'
            f'只有成交额放大而申购转化率不动，那是二级市场在打架。')


# ----------------------------------------------------------------- 主渲染
def render(r, intraday_used=True):
    rows, segs = r["rows"], r["segments"]
    live = r["live"] or {}
    has_live = bool(live.get("half"))
    AT = {a["code"]: a for a in r["attr"]}
    pool, pool2 = r["pool"], r["pool2"]
    NET = int(round(r["total"]["net"]))

    dates = [_d(x["date"]) for x in rows]
    nat = [x["nat"] for x in rows]
    cum = r["cumser"]
    hs = [x["hs"] for x in rows]

    # ---- 表格行 ----
    seg_rows = "".join(
        f'<tr><td>{s["label"]}</td>'
        f'<td class="num {"up" if s["chg"]>=0 else "down"}">{_sgn(s["chg"],2)}%</td>'
        f'<td class="num {"up" if s["nat"]>=0 else "down"}">{_sgn(s["nat"],1)}</td>'
        f'<td class="num">{(s["nat"]/r["total"]["net"]*100 if r["total"]["net"] else 0):.1f}%</td></tr>'
        for s in segs)

    daily_rows = ""
    for x in rows:
        tops = "、".join(f'{n} {v:+.0f}' for n, v in x["top"] if abs(v) >= 5) or "—"
        daily_rows += (f'<tr><td>{_d(x["date"])}</td>'
                       f'<td class="num">{x["hs"]:,.2f}</td>'
                       f'<td class="num {"up" if x["dhs"]>=0 else "down"}">{_sgn(x["dhs"],2)}%</td>'
                       f'<td class="num">{x["turn1"]:,.0f}</td>'
                       f'<td class="num">{_sgn(x["flow_all"],0)}</td>'
                       f'<td class="num">{x["w"]:.2f}</td>'
                       f'<td class="num {"up" if x["nat"]>=0 else "down"}">{_sgn(x["nat"],0)}</td>'
                       f'<td style="font-size:13px;color:#6b6b6b">{tops}</td></tr>')

    attr_rows = "".join(
        f'<tr><td><div class="nm">{a["name"]}'
        f'{"<span class=\'tag2\'>二档</span>" if a["tier"] == 2 else ""}</div>'
        f'<div class="cd">{a["code"]}</div></td><td>{a["idx"]}</td>'
        f'<td class="num">{a["scale"]:,.0f}</td><td class="num">{a["turn"]:,.0f}</td>'
        f'<td class="num">{a["flow"]:,.0f}</td><td class="num">{a["k"]*100:.0f}%</td>'
        f'<td class="num">{a["b"]:.2f}</td><td class="num">{a["r2"]:.2f}</td>'
        f'<td class="num up">{_sgn(a["nat"],0)}</td></tr>' for a in r["attr"])

    # ---- 最近 10 日 ----
    d10 = rows[-10:]
    mx10 = max(abs(x["nat"]) for x in d10) or 1
    d10_rows = ""
    for x in d10:
        w = abs(x["nat"]) / mx10 * 100
        c = "up" if x["nat"] >= 0 else "down"
        d10_rows += (f'<tr><td class="dt">{_d(x["date"])}</td>'
                     f'<td class="num {"up" if x["dhs"]>=0 else "down"}">{_sgn(x["dhs"],2)}%</td>'
                     f'<td class="bc"><div class="mb"><i class="{c}" style="width:{w:.0f}%"></i></div></td>'
                     f'<td class="num {c}">{_sgn(x["nat"],0)}</td></tr>')

    # ---- 实时状态 ----
    close = r.get("close")
    if close:
        st_text = "已入场" if close["entered"] else "未入场"
        st_cls = "on" if close["entered"] else "off"
        hs_line = ""
        if close.get("hs"):
            hs_line = (f'沪深300 {close["hs"]:,.2f}（<span class="{"up" if close["hs_chg"]>=0 else "down"}">'
                       f'{_sgn(close["hs_chg"], 2)}%</span>）<br>')
        left_body = f"""
    <div class="k">今日（{_d(close["date"])}）收盘状态</div>
    <div class="v {st_cls}">{st_text}</div>
    <div class="d">{hs_line}
      一档 {len(r["tier1"])} 只全天成交 <b>{close["turn1"]:,.0f} 亿</b><br>
      常态 {pool["v0"]:,.0f} 亿 · <b>{close["x_norm"]:.2f}×</b><br>
      近 5 日均量的 <b>{close["x5"]:.2f}×</b></div>
    <div class="est"><span class="el">全天成交额直接代入弹性公式</span>
      净申购 <b>{close["pred_flow"]:,.0f} 亿</b> · 归因国家队 <b>{close["pred_nat"]:,.0f} 亿</b></div>
    <div class="crit">判定标准：一档 {len(r["tier1"])} 只全天成交额 <b>≥ {close["threshold"]:,.0f} 亿</b><br>
      （常态 {pool["v0"]:,.0f} 亿的 2 倍）视为入场</div>"""
        if close["entered"]:
            read = (f'<b>盘面读数：全天放量过了 2 倍线，国家队大概率已经动手。</b>'
                    f'一档成交 {close["turn1"]:,.0f} 亿，是常态的 {close["x_norm"]:.1f} 倍，'
                    f'代入弹性公式推算净申购约 <b>{close["pred_flow"]:,.0f} 亿</b>、'
                    f'国家队归因约 <b>{close["pred_nat"]:,.0f} 亿</b>。')
        else:
            gap = max(0, close["threshold"] - close["turn1"])
            read = (f'<b>盘面读数：一档没有放到 2 倍线，成交额口径下不算国家队动手。</b>'
                    f'一档全天成交 {close["turn1"]:,.0f} 亿，是常态的 {close["x_norm"]:.2f} 倍、'
                    f'近 5 日均量的 {close["x5"]:.2f} 倍，离 2 倍线（{close["threshold"]:,.0f} 亿）'
                    f'还差 {gap:,.0f} 亿；代入公式推算净申购 {close["pred_flow"]:,.0f} 亿、'
                    f'国家队约 {close["pred_nat"]:,.0f} 亿，量级很小。')
        if r["tier2"]:
            hot2 = [e for e in close["per_etf"] if e["tier"] == 2 and e["x"] >= 2][:3]
            if hot2:
                read += (' 真正异常的是二档：' +
                         "、".join(f'{e["name"]} {e["x"]:.1f}×（{e["turn"]:.0f} 亿）' for e in hot2) +
                         f'。但二档 {len(r["tier2"])} 只的成交额与净申购已经脱钩，不能按成交额反推，'
                         f'只能等收盘份额确认——这一档今天全池成交 {close["turn2"]:,.0f} 亿。')
            else:
                read += (f' 二档 {len(r["tier2"])} 只合计 {close["turn2"]:,.0f} 亿，没有异常放量。')
        live_table = f"""<table>
  <tr><th>标的</th><th style="text-align:right">全天成交(亿)</th>
      <th style="text-align:right">常态(亿)</th><th style="text-align:left;padding-left:14px">放量程度</th>
      <th style="text-align:right">倍数</th><th style="text-align:right">档位</th></tr>
  {"".join(f'<tr><td><div class="nm">{e["name"]}'
           f'{"<span class=\'tag2\'>二档</span>" if e["tier"] == 2 else ""}</div>'
           f'<div class="cd">{e["code"]}</div></td>'
           f'<td class="num">{e["turn"]:,.1f}</td><td class="num">{e["v0"]:,.1f}</td>'
           f'<td><div class="bar"><i class="{"hot" if e["x"]>=2 else ("warm" if e["x"]>=1.3 else "")}" '
           f'style="width:{min(100, e["x"]/4*100):.0f}%"></i></div></td>'
           f'<td class="num {"up" if e["x"]>=2 else ""}">{e["x"]:.2f}×</td>'
           f'<td class="num" style="color:#9a9a9a">{e["tier"]} 档</td></tr>'
           for e in close["per_etf"])}
</table>"""
    elif has_live:
        st_text = "已入场" if live["entered"] else "未入场"
        st_cls = "on" if live["entered"] else "off"
        hs_line = ""
        if live.get("hs"):
            hs_line = (f'沪深300 {live["hs"]:,.2f}（<span class="{"up" if (live.get("hs_chg") or 0)>=0 else "down"}">'
                       f'{_sgn(live.get("hs_chg") or 0, 2)}%</span>）<br>')
        left_body = f"""
    <div class="k">{_d(live["date"])} {live.get("time") or "盘中"} 状态</div>
    <div class="v {st_cls}">{st_text}</div>
    <div class="d">{hs_line}
      一档 {len(r["tier1"])} 只半日成交 <b>{live["half"]:,.0f} 亿</b><br>
      近 5 日同期均值 {live["half5"]:,.0f} 亿 · <b>{live["x5"]:.2f}×</b></div>
    <div class="est"><span class="el">按半日成交折全天 {live["full"]:,.0f} 亿推算</span>
      净申购 <b>{live["pred_flow"]:,.0f} 亿</b> · 归因国家队 <b>{live["pred_nat"]:,.0f} 亿</b></div>
    <div class="crit">判定标准：一档 {len(r["tier1"])} 只全天成交额 <b>≥ {live["threshold"]:,.0f} 亿</b><br>
      （常态 {pool["v0"]:,.0f} 亿的 2 倍）视为入场</div>"""
        hottest = live["per_etf"][0] if live["per_etf"] else None
        if live["entered"]:
            read = (f'<b>盘面读数：放量已过 2 倍线，国家队大概率已经动手。</b>'
                    f'半日成交 {live["half"]:,.0f} 亿折全天 {live["full"]:,.0f} 亿，'
                    f'是常态的 {live["full"]/pool["v0"]:.1f} 倍，代入弹性公式推算净申购约 '
                    f'<b>{live["pred_flow"]:,.0f} 亿</b>、国家队归因约 <b>{live["pred_nat"]:,.0f} 亿</b>。')
        else:
            read = (f'<b>盘面读数：缩量，没有扫货痕迹。</b>半日成交 {live["half"]:,.0f} 亿只有近 5 日同期的 '
                    f'{live["x5"]:.2f} 倍，折全天 {live["full"]:,.0f} 亿，离 2 倍线（{live["threshold"]:,.0f} 亿）还差 '
                    f'{max(0, live["threshold"]-live["full"]):,.0f} 亿。'
                    + (f'全池最热的是 {hottest["name"]}，{hottest["x"]:.2f}× 自身常态，'
                       f'但绝对量只有 {hottest["full"]:.0f} 亿，' if hottest else '')
                    + f'量级够不上国家队日单只 50–190 亿的规模。')
        if r["tier2"]:
            short = {e["code"]: (e.get("short") or e["name"]) for e in r["universe"]["etfs"]}
            read += (f'二档 {len(r["tier2"])} 只（' +
                     "、".join(short[c] for c in r["tier2"]) +
                     '）盘中不能用成交额判定，只能等收盘看份额。')
        live_table = f"""<table>
  <tr><th>标的</th><th style="text-align:right">半日成交(亿)</th><th style="text-align:right">折全天(亿)</th>
      <th style="text-align:right">常态(亿)</th><th style="text-align:left;padding-left:14px">放量程度</th>
      <th style="text-align:right">倍数</th></tr>
  {"".join(f'<tr><td><div class="nm">{e["name"]}</div><div class="cd">{e["code"]}</div></td>'
           f'<td class="num">{e["half"]:,.1f}</td><td class="num">{e["full"]:,.1f}</td>'
           f'<td class="num">{e["v0"]:,.1f}</td>'
           f'<td><div class="bar"><i class="{"hot" if e["x"]>=2 else ("warm" if e["x"]>=1.3 else "")}" '
           f'style="width:{min(100, e["x"]/4*100):.0f}%"></i></div></td>'
           f'<td class="num {"up" if e["x"]>=2 else ""}">{e["x"]:.2f}×</td></tr>'
           for e in live["per_etf"])}
</table>"""
    else:
        st_text, st_cls = "待更新", "na"
        left_body = f"""
    <div class="k">今日盘中状态</div>
    <div class="v na">{st_text}</div>
    <div class="d">还没有抓今天的盘中成交额<br>
      判定标准：一档 {len(r["tier1"])} 只全天成交额 <b>≥ {2*pool["v0"]:,.0f} 亿</b>视为入场</div>
    <div class="crit">抓数看 <b>update.py plan</b><br>盘中快照用 <b>apply-intraday</b> 入库</div>"""
        read = '<b>盘中数据未更新。</b>下面所有数字都是收盘口径，等盘中快照入库后这一节会自动变成实时判定。'
        live_table = ""

    if close:
        title_sub = (f'今日（{_d(close["date"])}）收盘 · ' if close else "") + \
                    f'统计区间 {r["window"]["start"]} 至 {r["window"]["end"]}（{r["window"]["days"]} 个交易日）· 数据源 Wind'
    else:
        title_sub = (f'盘中监控截至 {_d(live["date"])} {live.get("time", "")} · ' if has_live else "") + \
                    f'统计区间 {r["window"]["start"]} 至 {r["window"]["end"]}（{r["window"]["days"]} 个交易日）· 数据源 Wind'


    if close:
        h2_1 = "一 · 实时监控：今天国家队动手了吗"
        h2_1sub = "收盘口径，成交额实盘，净申购待晚间"
    elif has_live:
        h2_1 = "一 · 实时监控：今天上午国家队动手了吗"
        h2_1sub = "盘中口径，非收盘数据"
    else:
        h2_1 = "一 · 实时监控：今天国家队动手了吗"
        h2_1sub = "暂无当日数据"
    # ---- 图表数据 ----
    bnames = [x["name"].replace("ETF", "") for x in r["diag"]]
    bbar = [{"value": x["b"], "r2": x["r2"], "tier": x["tier"],
             "itemStyle": {"color": RED if x["tier"] == 1 else "#c08a2e"}} for x in r["diag"]]
    poolpts = [[rows[i]["turn1"], rows[i]["flow1"], dates[i]] for i in range(len(rows))]
    xmax = max(p["turn1"] for p in rows) if rows else 800
    slope_end = round(pool["a"] + pool["b"] * xmax * 1.08, 1)
    top_b = sorted([a for a in r["attr"] if a["tier"] == 1], key=lambda a: -a["b"])[:2]

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>国家队买卖ETF监控 · {r["window"]["end"]}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#f4f2ee;color:#191919;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    font-size:16px;line-height:1.8;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1180px;margin:0 auto;padding:44px 30px 60px}}
.up{{color:{RED}}} .down{{color:{GREEN}}} .dim{{color:#9b9b9b}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}

header{{border-bottom:3px solid #191919;padding-bottom:18px;margin-bottom:30px}}
h1{{font-size:32px;line-height:1.3;letter-spacing:.01em;font-weight:800;color:{RED}}}
.sub{{margin-top:10px;font-size:14px;color:#757575}}

h2{{font-size:21px;font-weight:800;margin:38px 0 6px;padding-left:12px;border-left:4px solid {RED}}}
h2 span{{font-size:13px;font-weight:500;color:#9a9a9a;margin-left:8px;letter-spacing:.02em}}
.p{{margin:14px 0 20px;font-size:18px;line-height:2;color:#333}}
.p b{{color:#191919}}

.chart{{background:#fff;padding:18px 14px 8px;border-radius:3px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:14px}}
.cap{{font-size:14px;color:#8f8f8f;padding:0 6px 8px;line-height:1.7}}

table{{width:100%;border-collapse:collapse;background:#fff;font-size:15.5px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
th{{background:#191919;color:#fff;font-weight:600;font-size:13px;padding:9px 10px;text-align:right;letter-spacing:.02em}}
th:first-child,th:nth-child(2){{text-align:left}}
td{{padding:9px 10px;border-bottom:1px solid #eeeee9;vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
.nm{{font-weight:600;font-size:16px;line-height:1.4}}
.cd{{font-size:12.5px;color:#a0a0a0;font-variant-numeric:tabular-nums;letter-spacing:.02em}}

.formula{{background:#191919;color:#f2f0ec;padding:20px 24px;border-radius:3px;margin:14px 0;
    font-family:"SF Mono",Consolas,Menlo,monospace;font-size:15.5px;line-height:2.1;overflow-x:auto}}
.formula em{{color:#f0a49b;font-style:normal}}
.formula i{{color:#8fd0b6;font-style:normal}}

.live{{display:grid;grid-template-columns:minmax(236px,300px) 1fr;gap:0;background:#fff;border-radius:3px;
    box-shadow:0 1px 3px rgba(0,0,0,.06);overflow:hidden;margin-bottom:14px}}
.live .left{{background:#fbf9f2;border-left:4px solid {GREEN};padding:22px 22px 24px;
    display:flex;flex-direction:column}}
.live .left .est{{margin-top:15px;background:#fff;border:1px solid #eeeae1;border-radius:2px;
    padding:10px 12px;font-size:14px;color:#6b6b6b;line-height:1.85}}
.live .left .est b{{color:#191919;font-variant-numeric:tabular-nums}}
.live .left .est .el{{display:block;font-size:12px;color:#9a958a}}
.live .left .crit{{margin-top:auto;padding-top:14px;font-size:13px;color:#8f8b80;
    line-height:1.85;border-top:1px solid #eeeae1}}
.live .left .crit b{{color:#6b675e}}
.live .left.on{{border-left-color:{RED}}}
.live .left .k{{font-size:12.5px;color:#8f8b80;letter-spacing:.06em;margin-bottom:8px}}
.live .left .v{{font-size:44px;font-weight:800;line-height:1.05;letter-spacing:-1px}}
.live .left .v.off{{color:{GREEN}}}
.live .left .v.on{{color:{RED}}}
.live .left .v.na{{color:#8f8b80;font-size:30px}}
.live .left .d{{font-size:15px;color:#6b6b6b;margin-top:15px;line-height:1.9}}
.live .right{{padding:16px 20px 18px}}
.live .right .t{{font-size:14.5px;font-weight:700;margin-bottom:2px}}
.live .right .ts{{font-size:12px;color:#9a9a9a;margin-bottom:8px}}
.mini{{width:100%;border-collapse:collapse;font-size:14.5px;background:none;box-shadow:none}}
.mini td{{padding:4px 6px 4px 0;border-bottom:1px solid #f4f2ed;vertical-align:middle}}
.mini tr:last-child td{{border-bottom:none}}
.mini .hd td{{font-size:11.5px;color:#a8a8a8;border-bottom:1px solid #eae7df;padding-bottom:4px}}
.mini .dt{{width:64px;color:#3a3a3a;font-variant-numeric:tabular-nums}}
.mini .bc{{width:39%;padding-right:12px}}
.mb{{height:6px;background:#f0eee8;border-radius:3px;overflow:hidden}}
.mb i{{display:block;height:100%}}
.mb i.up{{background:#d99f98}}
.mb i.down{{background:#8fc6b0}}
.bar{{height:7px;background:#eeece6;border-radius:4px;overflow:hidden;width:100%;min-width:70px}}
.bar i{{display:block;height:100%;background:#c3c0b8}}
.bar i.warm{{background:#e0a03c}}
.bar i.hot{{background:{RED}}}

.tag2{{display:inline-block;margin-left:6px;padding:0 6px;font-size:11px;font-weight:600;
    color:#8a6d1f;background:#f6ecd6;border-radius:2px;vertical-align:1px}}

.note{{background:#fffdf5;border:1px solid #f0e6c8;padding:22px 26px;font-size:16.5px;color:#5d5647;
    line-height:2;border-radius:3px;margin-top:14px}}
.note b{{color:#8a6d1f}}
.disc{{margin-top:34px;padding-top:18px;border-top:1px solid #dcd8d0;font-size:12.5px;color:#9a9a9a;line-height:1.9}}
@media(max-width:820px){{h1{{font-size:27px}}.wrap{{padding:26px 16px}}table{{font-size:13.5px}}
  .live .left .v{{font-size:33px}}.live .left{{padding:18px 16px 20px}}
  .live .right{{padding:14px 14px 16px}}.mini{{font-size:13.5px}}.mini .bc{{width:32%}}
  .p{{font-size:17px}}.note{{font-size:15.5px;padding:18px 20px}}.formula{{font-size:14.5px}}}}
@media(max-width:520px){{.live{{grid-template-columns:1fr}}.mini .bc{{width:36%}}
  .p{{font-size:16.5px}}table{{font-size:13px}}.nm{{font-size:14.5px}}.cd{{font-size:12px}}}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>国家队资金 · ETF 抄底监控</h1>
  <div class="sub">{title_sub}</div>
</header>

<h2>{h2_1}<span>{h2_1sub}</span></h2>
<p class="p">
判断只看一件事：<b>成交额有没有炸出来</b>。国家队申购一笔动辄单只 50–190 亿，钱进场必然先把成交额顶上去，
所以用<b>一档 {len(r["tier1"])} 只</b>的成交额，对比它们各自的「常态成交额」，就够看出有没有人动手。
</p>
<div class="live">
  <div class="left {st_cls}">{left_body}
  </div>
  <div class="right">
    <div class="t">最近 10 个交易日国家队动态</div>
    <div class="ts">估算净买入（亿元），横条按绝对值缩放 · 红买绿卖</div>
    <table class="mini">
      <tr class="hd"><td>日期</td><td style="text-align:right">涨跌</td><td></td>
          <td style="text-align:right">净买入(亿)</td></tr>
      {d10_rows}
    </table>
  </div>
</div>
<p class="p" style="margin:16px 0 14px">{read}</p>
{live_table}

<h2>二 · 这段时间国家队买卖节奏<span>{len(segs)} 段波段，累计净买入 {NET:,.0f} 亿</span></h2>
<div class="chart"><div id="c3" style="width:100%;height:400px"></div>
  <div class="cap">柱：每日国家队净买入估算（红买绿卖，亿元，左轴）｜线：沪深300 收盘（右轴）。数据源 Wind。</div>
</div>
<div class="chart"><div id="c4" style="width:100%;height:300px"></div>
  <div class="cap">国家队净买入累计曲线（估算值，亿元）。数据源 Wind。</div>
</div>
<table style="margin-top:14px">
  <tr><th>阶段</th><th style="text-align:right">区间涨跌</th><th style="text-align:right">净买入(亿)</th>
      <th style="text-align:right">占全期</th></tr>
  {seg_rows}
</table>
<details style="margin-top:12px">
  <summary style="cursor:pointer;font-size:14px;color:{RED};font-weight:600;padding:8px 2px">
    展开每日明细（图 / 表切换）</summary>
  <table style="margin-top:8px">
    <tr><th>日期</th><th style="text-align:right">沪深300</th><th style="text-align:right">涨跌</th>
        <th style="text-align:right">一档成交额(亿)</th><th style="text-align:right">全池净申购(亿)</th>
        <th style="text-align:right">归因系数</th><th style="text-align:right">国家队(亿)</th>
        <th style="text-align:left;padding-left:14px">当日主力标的</th></tr>
    {daily_rows}
  </table>
</details>
<p class="p">{_rhythm_text(r)}</p>

<h2>三 · 哪些 ETF 算"国家队标的"<span>{len(r["diag"])} 只，一档看成交额，二档只能看份额</span></h2>
<p class="p">
国家队买 ETF 走<b>一级市场申购</b>，一笔几十上百亿，所以它的工具必然是头部宽基。按季报口径，池子分
{len(set(e.get("group","") for e in r["universe"]["etfs"]))} 组：
{_groups_text(r)}。合计 <b>{len(r["diag"])} 只</b>、规模 <b>{r["all"]["scale"]:,.0f} 亿</b>。
</p>
<p class="p">
名单是固定的，但<b>不能用同一种方法去测</b>。把「成交额 → 净申购额」在 {r["window"]["days"]} 个交易日上做线性回归，
得到弹性 <b>b</b> 和拟合度 <b>R²</b>：<b>b 接近 1、R² 接近 1，说明成交额基本就是申购的镜像</b>，盘中放量能直接读成国家队动手；
b 低、R² 低，说明成交额里混了大量二级换手，<b>用成交额反推会严重失真</b>——但份额变动本身仍是实测的，照样能算，只是必须等收盘。
</p>
<div class="chart"><div id="c1" style="width:100%;height:{max(300, 40*len(r["diag"])+80)}px"></div>
  <div class="cap">横轴：成交额-净申购弹性 b（柱右侧标注 R²）｜ 红柱 = 一档（成交额可反推）｜ 金柱 = 二档（只能看份额）｜
  虚线 b={TIER_B_LABEL} 为一档线。数据源 Wind，{r["window"]["start"]} 至 {r["window"]["end"]} 日频。</div>
</div>
<p class="p">{_tier_text(r)}</p>
{_quiet_text(r)}

<h2>四 · 分标的明细<span>{r["window"]["start"]} 至 {r["window"]["end"]}</span></h2>
<table>
  <tr><th>标的</th><th>跟踪指数</th><th style="text-align:right">规模(亿)</th><th style="text-align:right">成交额(亿)</th>
      <th style="text-align:right">净申购(亿)</th><th style="text-align:right">转化率</th>
      <th style="text-align:right">弹性 b</th><th style="text-align:right">R²</th>
      <th style="text-align:right">国家队归因(亿)</th></tr>
  {attr_rows}
</table>
<p class="p" style="font-size:15.5px;color:#8a8a8a">规模 = 流通份额 × 最新收盘价；国家队归因 = 逐日「净申购 × 该档放量系数 w」后累加，
一档平均 w ≈ {r["t1"]["nat"]/r["t1"]["flow"] if r["t1"]["flow"] else 0:.2f}、二档 ≈ {r["t2"]["nat"]/r["t2"]["flow"] if r["t2"]["flow"] else 0:.2f}。</p>

<h2>五 · 成交量与国家队买入量的定量关系<span>这是整套估算的地基</span></h2>
<div class="formula">
净申购额(亿) = <em>{pool["a"]:,.1f}</em> + <em>{pool["b"]:.3f}</em> × 当日成交额(亿)　　R² = {pool["r2"]:.3f}<br>
<span style="color:#8a8a8a">其中「常态成交额」= {pool["v0"]:,.0f} 亿 —— 成交额低于这条线时，申购接近于零，全是二级换手</span><br>
国家队净买入(亿) = 净申购额 × <i>w</i>　　<i>w</i> = {W_MIN_LABEL} ~ {W_MAX_LABEL}（随放量程度递增，均值 {r["total"]["avg_w"]:.2f}）
{f'<br><span style="color:#8a8a8a">二档 {len(r["tier2"])} 只合并 b={pool2["b"]:.2f}、R²={pool2["r2"]:.2f}，已与申购脱钩，<b>不参与盘中反推</b>；'
 f'常态成交额取中位数 {pool2["v0"]:,.0f} 亿，只用于收盘后的份额实测</span>' if r["tier2"] else ''}
</div>
<div class="chart"><div id="c2" style="width:100%;height:390px"></div>
  <div class="cap">一档 {len(r["tier1"])} 只合计：{r["window"]["days"]} 个交易日的「成交额 vs 净申购额」散点与回归线。数据源 Wind。</div>
</div>
<p class="p">
这条关系解释力很强（R²={pool["r2"]:.2f}）：<b>成交额每多出 1 亿，净申购就多 {pool["b"]:.2f} 亿</b>。
换句话说，宽基 ETF 的放量，八成以上就是申购带来的，看成交额放大基本等于看国家队动手。落到单只 ETF 上更极端——
{"、".join(f'{a["name"]} 的 b={a["b"]:.2f}、R²={a["r2"]:.3f}' for a in top_b)}，<b>成交额和申购几乎是同一件事</b>。
</p>
<p class="p">
归因系数 w 为什么不是 1：净申购里除了国家队，还有保险、私募、券商自营和散户的申购。放量越极端，国家队占比越高，
所以 w 设定为 0.35（成交额仅略高于常态）到 0.75（放量 3.7 倍以上）之间滑动，全期均值 {r["total"]["avg_w"]:.2f}。
按此口径，{len(r["diag"])} 只标的合计 {r["all"]["flow"]:,.0f} 亿净申购中，<b>可归因于国家队的约 {NET:,} 亿</b>
（一档 {r["t1"]["nat"]:,.0f} 亿{f' + 二档 {r["t2"]["nat"]:,.0f} 亿' if r["tier2"] else ''}）。
</p>

<h2>六 · 这套算法靠不靠谱<span>自己拆自己的台</span></h2>
<div class="note">
{_caveat_text(r)}
</div>

<div class="disc">
数据来源：Wind（场内基金日 K、ETF 日频资金净流入额、指数日 K），统计区间 {r["window"]["start"]} 至 {r["window"]["end"]} 收盘
{f'，盘中数据截至 {_d(live["date"])} {live.get("time","")}' if has_live else ''}。
净申购额为 Wind ETF 专项指标，非交易所原始份额变动，可能存在口径差异。国家队归因金额为模型估算值，不构成对任何机构实际操作的认定。<br><br>
<b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。
任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>

</div>
<script>
var DATES = {json.dumps(dates, ensure_ascii=False)};
var NAT = {json.dumps(nat)};
var CUM = {json.dumps(cum)};
var HS = {json.dumps(hs)};
var BNAMES = {json.dumps(bnames, ensure_ascii=False)};
var BBAR = {json.dumps(bbar, ensure_ascii=False)};
var AX = {{
  axisLine: {{ lineStyle: {{ color: '#d8d5cd' }} }},
  axisLabel: {{ color: '#7a7a7a', fontSize: 11 }},
  splitLine: {{ lineStyle: {{ color: '#f0eee9' }} }}
}};

// 图1 弹性条形图
echarts.init(document.getElementById('c1')).setOption({{
  tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }},
    formatter: function(p) {{
      var d = p[0].data;
      return p[0].name + '<br>弹性 b: ' + d.value.toFixed(3) + '<br>R²: ' + d.r2.toFixed(3);
    }} }},
  grid: {{ left: 138, right: 96, top: 22, bottom: 30 }},
  xAxis: {{ type: 'value', min: 0, max: {max(1.2, max(x["b"] for x in r["diag"]) + 0.15):.2f}, ...AX }},
  yAxis: {{ type: 'category', data: BNAMES, inverse: true,
            axisLabel: {{ color: '#2c2c2c', fontSize: 11.5 }},
            axisLine: {{ lineStyle: {{ color: '#d8d5cd' }} }}, axisTick: {{ show: false }} }},
  series: [{{
    type: 'bar', data: BBAR, barWidth: '56%',
    label: {{ show: true, position: 'right', fontSize: 11, color: '#5c5c5c',
      formatter: function(p) {{
        return p.data.value.toFixed(2) + '  R²' + p.data.r2.toFixed(2) + (p.data.tier === 2 ? '  二档' : '');
      }} }},
    markLine: {{ silent: true, symbol: 'none',
      lineStyle: {{ color: '#8a8378', type: 'dashed' }},
      data: [{{ xAxis: {TIER_B_LABEL}, label: {{ formatter: '一档线 b={TIER_B_LABEL}', fontSize: 10, color: '#8a8378',
        position: 'start', distance: 6 }} }}] }}
  }}]
}});

// 图2 成交额 vs 净申购
var pts = {json.dumps(poolpts)};
echarts.init(document.getElementById('c2')).setOption({{
  tooltip: {{ formatter: function(p) {{
    if (p.seriesType === 'line') return '回归线';
    return p.data[2] + '<br>成交额: ' + p.data[0] + ' 亿<br>净申购: ' + p.data[1] + ' 亿';
  }} }},
  grid: {{ left: 66, right: 40, top: 30, bottom: 58 }},
  xAxis: {{ type: 'value', name: '当日成交额(亿)', nameLocation: 'middle', nameGap: 30,
            nameTextStyle: {{ color: '#7a7a7a', fontSize: 11 }}, ...AX }},
  yAxis: {{ type: 'value', name: '当日净申购(亿)', nameLocation: 'end', nameGap: 10,
            nameTextStyle: {{ color: '#7a7a7a', fontSize: 11 }}, ...AX }},
  series: [
    {{ type: 'scatter', data: pts,
       symbolSize: function(v) {{ return v[1] > 200 ? 15 : 9; }},
       itemStyle: {{ color: '{RED}', opacity: 0.7 }} }},
    {{ type: 'line', showSymbol: false, data: [[0, {pool["a"]}], [{int(xmax*1.08)}, {slope_end}]],
       lineStyle: {{ color: '#191919', width: 2 }}, itemStyle: {{ color: '#191919' }} }}
  ]
}});

// 图3 每日净买入 + 沪深300
echarts.init(document.getElementById('c3')).setOption({{
  tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
  legend: {{ data: ['国家队净买入', '沪深300'], right: 6, top: 0, itemGap: 18,
            textStyle: {{ fontSize: 12, color: '#3a3a3a' }} }},
  grid: {{ left: 58, right: 58, top: 42, bottom: 42 }},
  xAxis: {{ type: 'category', data: DATES, axisLabel: {{ color: '#7a7a7a', fontSize: 10.5, interval: 1 }},
            axisLine: {{ lineStyle: {{ color: '#d8d5cd' }} }}, axisTick: {{ show: false }} }},
  yAxis: [
    {{ type: 'value', name: '亿元', nameTextStyle: {{ color: '#7a7a7a', fontSize: 11 }}, ...AX }},
    {{ type: 'value', scale: true, splitLine: {{ show: false }},
       axisLine: {{ lineStyle: {{ color: '#d8d5cd' }} }}, axisLabel: {{ color: '#7a7a7a', fontSize: 11 }} }}
  ],
  series: [
    {{ name: '国家队净买入', type: 'bar', data: NAT.map(function(v) {{
         return {{ value: v, itemStyle: {{ color: v >= 0 ? '{RED}' : '{GREEN}' }} }};
       }}), barWidth: '56%', itemStyle: {{ color: '{RED}' }} }},
    {{ name: '沪深300', type: 'line', yAxisIndex: 1, data: HS, smooth: true,
       symbol: 'circle', symbolSize: 4, lineStyle: {{ color: '#7a7a7a', width: 1.6 }},
       itemStyle: {{ color: '#7a7a7a' }} }}
  ]
}});

// 图4 累计
echarts.init(document.getElementById('c4')).setOption({{
  tooltip: {{ trigger: 'axis', formatter: function(p) {{
    return p[0].axisValue + '<br>累计净买入: ' + p[0].data + ' 亿';
  }} }},
  grid: {{ left: 58, right: 34, top: 24, bottom: 42 }},
  xAxis: {{ type: 'category', data: DATES, boundaryGap: false,
            axisLabel: {{ color: '#7a7a7a', fontSize: 10.5, interval: 1 }},
            axisLine: {{ lineStyle: {{ color: '#d8d5cd' }} }}, axisTick: {{ show: false }} }},
  yAxis: {{ type: 'value', name: '亿元', nameTextStyle: {{ color: '#7a7a7a', fontSize: 11 }}, ...AX }},
  series: [{{
    type: 'line', data: CUM, smooth: true, symbol: 'none',
    lineStyle: {{ color: '{RED}', width: 2.2 }},
    areaStyle: {{ color: {{ type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
      colorStops: [{{ offset: 0, color: 'rgba(200,52,44,0.22)' }},
                   {{ offset: 1, color: 'rgba(200,52,44,0.02)' }}] }} }}
  }}]
}});
</script>
</body>
</html>"""


TIER_B_LABEL = "0.8"
W_MIN_LABEL, W_MAX_LABEL = "0.35", "0.75"
