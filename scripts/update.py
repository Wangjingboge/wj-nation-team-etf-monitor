# -*- coding: utf-8 -*-
"""国家队买卖ETF监控（望京博格版）—— 统一入口。

    python scripts/update.py status             # 历史库覆盖到哪一天、缺什么
    python scripts/update.py plan                # 输出「要抓哪些数据」的清单（含 MCP 参数）
    python scripts/update.py apply inbox.json    # 把抓回来的增量并入库
    python scripts/update.py apply-intraday s.json
    python scripts/update.py compute             # 重算模型 → out/result.json
    python scripts/update.py build               # 渲染 → out/看板.html
    python scripts/update.py export              # 导出 2160 宽长图
    python scripts/update.py all                 # plan 之后一键算+渲染+出图

设计要点：历史库只存「日频原始值」，模型每次从库重算。
所以新增一天数据只是往库里补一条，不需要改动任何脚本。
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store
import model
import render

DEFAULT_WINDOW = 21
DEFAULT_SEG = 5
HTML_NAME = "国家队买卖ETF监控.html"


def _u():
    u = store.load("universe.json")
    if not u or not u.get("etfs"):
        raise SystemExit("缺 data/universe.json 或标的列表为空，先补标的定义。")
    return u


def _d():
    d = store.load("daily.json", {"benchmark": {}, "etfs": {}})
    return d


# ------------------------------------------------------------------ status
def cmd_status(args):
    u, d = _u(), _d()
    codes = [e["code"] for e in u["etfs"]]
    all_dates = store.dates_of(d)
    print(f"历史库：{store._path('daily.json')}")
    if not all_dates:
        print("  （空库）")
        print("\n下一步：python scripts/update.py plan")
        return
    print(f"  覆盖 {all_dates[0]} ~ {all_dates[-1]}，共 {len(all_dates)} 个交易日")
    print(f"  基准：{(d.get('benchmark') or {}).get('name','')} "
          f"{(d.get('benchmark') or {}).get('code','')}  收盘 {len((d.get('benchmark') or {}).get('close',{}))} 天")
    print()
    print(f"  {'代码':<12}{'简称':<22}{'成交额天':>7}{'净申购天':>8}  {'最后一天':<12}")
    gaps = {}
    for e in u["etfs"]:
        rec = d.get("etfs", {}).get(e["code"], {})
        t = rec.get("turnover", {})
        f = rec.get("flow", {})
        last = max(set(t) & set(f)) if (t and f) else "-"
        print(f"  {e['code']:<12}{(e.get('short') or e['name']):<22}{len(t):>7}{len(f):>8}  {last:<12}")
        if last != "-":
            gaps[e["code"]] = last
    common = store.common_dates(d, codes)
    if common:
        print(f"\n  全池齐备的交易日：{len(common)} 天（{common[0]} ~ {common[-1]}）")
    missing = len(all_dates) - len(common)
    if missing > 0:
        print(f"  有 {missing} 天部分标的缺失，建模会自动只用齐备的日子")
    intra = store.load("intraday.json")
    if intra:
        print(f"  盘中快照：{intra.get('date')} {intra.get('time','')}"
              f"（{len(intra.get('half') or {})} 只）")
    else:
        print("  盘中快照：无")


# -------------------------------------------------------------------- plan
def cmd_plan(args):
    u, d = _u(), _d()
    codes = [e["code"] for e in u["etfs"]]
    begin_db = store.load("daily.json") or {}
    last_dt = store.last_date(begin_db, codes)
    end = args.end or store.today_str()
    begin = args.begin or (None if last_dt is None else _next_day(last_dt))

    if last_dt and begin is None:
        begin = _next_day(last_dt)
    if begin is None:
        raise SystemExit("库里没有历史，请用 --begin 指定起始日（建议往前推 30 个交易日）")

    up_to_date = last_dt and last_dt >= end
    plan = {
        "generated": store.now_str(),
        "local": {"first": (store.common_dates(d, codes) or ["-"])[0],
                  "last": last_dt, "days": len(store.common_dates(d, codes))},
        "target": {"begin": begin, "end": end, "incremental": last_dt is not None},
        "note": ("本地已覆盖到 " + str(last_dt) + "，只需抓 " + begin + " 之后的增量") if last_dt
                else "首次建库，建议 --begin 取 30 个交易日前的日期",
        "calls": [],
    }

    if up_to_date and not args.force:
        plan["calls"] = []
        plan["note"] = f"本地已覆盖到 {last_dt}（>= {end}），无需抓取。要强制重抓加 --force。"
    else:
        need = []
        for e in u["etfs"]:
            rec = (d.get("etfs") or {}).get(e["code"]) or {}
            lc = max(set(rec.get("turnover", {})) & set(rec.get("flow", {}))) \
                if rec.get("turnover") and rec.get("flow") else None
            if args.only_missing and lc and lc >= end:
                continue
            b = _next_day(lc) if lc else begin
            need.append((e, b))
        for e, b in need:
            plan["calls"].append({
                "tool": "mcp__wind-fund-data__get_fund_kline",
                "label": f'{e.get("short") or e["name"]} 日K',
                "params": {"windcode": e["code"], "begin_date": b, "end_date": end, "period": "10"},
                "extract": "取每根 K 线的 amount（成交额，元）→ turnover",
            })
        for e, b in need:
            plan["calls"].append({
                "tool": "mcp__wind-fund-data__get_fund_performance",
                "label": f'{e.get("short") or e["name"]} 每日净流入',
                "params": {"question": f'查询{e["name"]}({e["code"]})最近1个月每个交易日的基金资金净流入额'},
                "extract": "取每日「基金资金净流入额」，单位换算成亿元 → flow（只能单只查，多只会截断）",
            })
        plan["calls"].append({
            "tool": "mcp__wind-index-data__get_index_kline",
            "label": "沪深300 日K",
            "params": {"windcode": (d.get("benchmark") or {}).get("code", "000300.SH"),
                       "begin_date": begin, "end_date": end, "period": "10"},
            "extract": "取 close → benchmark.close",
        })
        plan["calls"].append({
            "tool": "mcp__wind-fund-data__get_fund_price_indicators",
            "label": "份额与价格（月度一次即可，用于规模）",
            "params": {"windcode": ",".join(e["code"] for e in u["etfs"]),
                       "indexes": "中文简称,最新交易日,最新成交价,流通份额,最新净值,涨跌幅"},
            "extract": "流通份额 × 最新成交价 → 每只的 scale（亿元），回填 data/universe.json",
            "optional": True,
        })

    print(json.dumps(plan, ensure_ascii=False, indent=1))
    store.ensure_dirs()
    with open(os.path.join(store.OUT_DIR, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    if plan["calls"]:
        print(f"\n# 共 {len(plan['calls'])} 个调用。抓完写成 inbox.json 后："
              f"\n#   python scripts/update.py apply inbox.json", file=sys.stderr)


def _next_day(s):
    import datetime
    y, m, dd = map(int, s.split("-"))
    return (datetime.date(y, m, dd) + datetime.timedelta(days=1)).isoformat()


# ------------------------------------------------------------------- apply
def cmd_apply(args):
    u, d = _u(), _d()
    payload = _read_payload(args.path)
    stat, warns = store.merge_inbox(payload, u, d)
    print(f"入库：成交额 {stat['turnover']} 条、净申购 {stat['flow']} 条、基准 {stat['benchmark']} 条")
    for w in warns:
        print("  ! " + w)
    if warns:
        print(f"（{len(warns)} 条警告，已跳过的问题数据不会影响库）")
    store.audit(f"apply {args.path} -> {stat} warns={len(warns)}")
    codes = [e["code"] for e in u["etfs"]]
    common = store.common_dates(d, codes)
    if common:
        print(f"库内全池齐备：{len(common)} 天（{common[0]} ~ {common[-1]}）")
        print("下一步：python scripts/update.py compute")


def cmd_apply_intraday(args):
    u = _u()
    snap = _read_payload(args.path)
    half = snap.get("half") or {}
    known = {e["code"] for e in u["etfs"]}
    bad = [c for c in half if c not in known]
    if bad:
        print(f"  ! 不在 universe 里的代码已忽略：{bad}")
        half = {c: v for c, v in half.items() if c in known}
    snap["half"] = {c: float(v) for c, v in half.items()}
    snap.setdefault("date", store.today_str())
    snap.setdefault("ratio", 0.55)
    snap["updated"] = store.now_str()
    store.save_intraday(snap)
    store.audit(f"apply-intraday {snap.get('date')} {len(half)} 只")
    print(f"盘中快照入库：{snap['date']} {snap.get('time','')}，{len(half)} 只，"
          f"半日合计 {sum(snap['half'].values()):,.1f} 亿，折全天系数 {snap['ratio']}")
    print("下一步：python scripts/update.py all")


def _read_payload(path):
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    raise SystemExit("只支持 .json 文件")


# ---------------------------------------------------------------- compute
def cmd_compute(args):
    u, d = _u(), _d()
    intra = store.load("intraday.json")
    r = model.compute(u, d, intra, window=args.window or DEFAULT_WINDOW, seg=args.seg or DEFAULT_SEG)
    store.ensure_dirs()
    out = os.path.join(store.OUT_DIR, "result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    t = r["total"]
    print(f"窗口 {r['window']['days']} 天：{r['window']['start']} ~ {r['window']['end']}"
          f"（库里还有 {r['window']['total_available']} 天可用）")
    print(f"一档 {len(r['tier1'])} 只 / 二档 {len(r['tier2'])} 只")
    print(f"一档组合：净申购 = {r['pool']['a']:,.1f} + {r['pool']['b']:.3f}×成交额，"
          f"R²={r['pool']['r2']:.3f}，常态 {r['pool']['v0']:,.0f} 亿")
    print(f"全池净申购 {r['all']['flow']:,.0f} 亿 → 归因国家队 {t['net']:,.0f} 亿"
          f"（买 {t['buy']:,.0f} / 卖 {t['sell']:,.0f}，{t['pos_days']}/{r['window']['days']} 天净买）")
    print(f"峰值 {t['peak']['date']} {t['peak']['nat']:,.0f} 亿（成交额常态 {t['peak']['x']:.1f} 倍）")
    if r.get("close"):
        cl = r["close"]
        print(f"收盘 {cl['date']}：一档全天 {cl['turn1']:,.0f} 亿 · 常态 {cl['x_norm']:.2f}× · "
              f"近5日 {cl['x5']:.2f}× · {'已入场' if cl['entered'] else '未入场'}"
              f"（二档 {cl['turn2']:,.0f} 亿，待份额确认）")
    if r.get("live") and r["live"].get("half"):
        lv = r["live"]
        print(f"盘中 {lv['date']} {lv.get('time','')}：半日 {lv['half']:,.0f} 亿 · {lv['x5']:.2f}× · "
              f"{'已入场' if lv['entered'] else '未入场'}")
    print(f"→ {out}")


# ------------------------------------------------------------------ build
def cmd_build(args):
    res = os.path.join(store.OUT_DIR, "result.json")
    if not os.path.exists(res):
        raise SystemExit("先跑 compute")
    with open(res, encoding="utf-8") as f:
        r = json.load(f)
    html = render.render(r)
    out = os.path.join(store.OUT_DIR, HTML_NAME)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"→ {out}（{len(html):,} 字符）")


# ----------------------------------------------------------------- export
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def _chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def cmd_export(args):
    html = os.path.join(store.OUT_DIR, HTML_NAME)
    if not os.path.exists(html):
        raise SystemExit("先跑 build")
    exe = _chrome()
    if not exe:
        raise SystemExit("没找到 Chrome/Edge，无法导出长图。可直接用浏览器打开 HTML 截图。")
    raw = os.path.join(store.OUT_DIR, "_raw.png")
    height = args.height
    cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           f"--window-size=1080,{height}", "--force-device-scale-factor=2",
           "--virtual-time-budget=10000", f"--screenshot={raw}",
           "file:///" + html.replace("\\", "/")]
    subprocess.run(cmd, capture_output=True)
    if not os.path.exists(raw):
        raise SystemExit("截图为空，检查 Chrome 是否可无头运行")
    out = os.path.join(store.OUT_DIR, "国家队买卖ETF看板_长图.png")
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(raw).convert("RGB")
        a = np.array(im).astype(int)
        bg = np.array([244, 242, 238])
        rows = np.where((np.abs(a - bg).max(axis=2) > 18).any(axis=1))[0]
        bottom = int(rows[-1]) if len(rows) else im.size[1] - 1
        im.crop((0, 0, im.size[0], min(im.size[1], bottom + 50))).save(out)
        os.remove(raw)
        print(f"→ {out}（{Image.open(out).size[0]}×{Image.open(out).size[1]}）")
    except ImportError:
        os.replace(raw, out)
        print(f"→ {out}（未裁白边：缺 Pillow/numpy）")


def cmd_all(args):
    cmd_compute(args)
    cmd_build(args)
    if not args.no_image:
        try:
            cmd_export(args)
        except SystemExit as e:
            print(f"（长图跳过：{e}）")


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="国家队买卖ETF监控（望京博格版）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("plan")
    p.add_argument("--begin", help="起始日 YYYY-MM-DD（默认接在本地最后一天之后）")
    p.add_argument("--end", help="结束日（默认今天）")
    p.add_argument("--only-missing", action="store_true", help="只列还没更新到 end 的标的")
    p.add_argument("--force", action="store_true", help="即使已覆盖也重新给清单")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply")
    p.add_argument("path")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("apply-intraday")
    p.add_argument("path")
    p.set_defaults(func=cmd_apply_intraday)

    for name, fn in (("compute", cmd_compute), ("build", cmd_build), ("export", cmd_export)):
        p = sub.add_parser(name)
        p.add_argument("--window", type=int, default=0, help="建模窗口交易日数，0=全部（默认 21）")
        p.add_argument("--seg", type=int, default=0, help="阶段切块天数，默认 5")
        p.add_argument("--height", type=int, default=9000)
        p.set_defaults(func=fn)

    p = sub.add_parser("all")
    p.add_argument("--window", type=int, default=0)
    p.add_argument("--seg", type=int, default=0)
    p.add_argument("--height", type=int, default=9000)
    p.add_argument("--no-image", action="store_true")
    p.set_defaults(func=cmd_all)

    args = ap.parse_args()
    if not getattr(args, "window", 0):
        args.window = DEFAULT_WINDOW
    if not getattr(args, "seg", 0):
        args.seg = DEFAULT_SEG
    args.func(args)


if __name__ == "__main__":
    main()
