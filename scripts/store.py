# -*- coding: utf-8 -*-
"""数据层：历史库的读取、原子写、自动备份、审计流水、增量合并与校验。

历史库结构（data/）：
  universe.json  标的与基准的静态信息（代码/简称/跟踪指数/分组/份额/价格）
  daily.json     日频历史（成交额、净申购、基准收盘），按日期字典存，天然支持增量追加
  intraday.json  最近一次盘中快照（半日成交额等），可缺失
  audit.log      追加式流水，记录每次入库的来源与条数
  backup/        每次写入前的自动快照（保留最近 KEEP 份）
"""
import json
import os
import shutil
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
AUDIT_PATH = os.path.join(DATA_DIR, "audit.log")
OUT_DIR = os.path.join(ROOT, "out")

SCHEMA = 1
KEEP_BACKUP = 30


# ---------------------------------------------------------------- 基础工具
def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.date.today().isoformat()


def ensure_dirs():
    for d in (DATA_DIR, BACKUP_DIR, OUT_DIR):
        os.makedirs(d, exist_ok=True)


def _path(name):
    return os.path.join(DATA_DIR, name)


def load(name, default=None):
    """读 JSON；不存在返回 default。"""
    p = _path(name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj, backup=True):
    """原子写：先写同目录临时文件再 os.replace，避免写一半断电损坏。"""
    ensure_dirs()
    p = _path(name)
    if backup and os.path.exists(p):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = os.path.splitext(name)[0]
        shutil.copy2(p, os.path.join(BACKUP_DIR, f"{stem}.{ts}.json"))
        _prune_backups(stem)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def _prune_backups(stem):
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(stem + "."))
    for f in files[:-KEEP_BACKUP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass


def audit(msg):
    ensure_dirs()
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{now_str()}] {msg}\n")


def load_all():
    return {
        "universe": load("universe.json", {"etfs": []}),
        "daily": load("daily.json", {"benchmark": {}, "etfs": {}}),
        "intraday": load("intraday.json", None),
    }


# ---------------------------------------------------------------- 历史库访问
def dates_of(daily):
    """历史库覆盖的全部交易日（升序）。以基准与任一只 ETF 出现过的日期并集为准。"""
    ds = set(daily.get("benchmark", {}).get("close", {}).keys())
    for c, v in daily.get("etfs", {}).items():
        ds |= set(v.get("turnover", {}).keys())
    return sorted(ds)


def common_dates(daily, codes):
    """所有指定标的都有成交额与净申购的交易日（升序）——建模只能用这些天。"""
    ds = None
    for c in codes:
        v = daily.get("etfs", {}).get(c)
        if not v:
            return []
        s = set(v.get("turnover", {})) & set(v.get("flow", {}))
        ds = s if ds is None else (ds & s)
    return sorted(ds or [])


def last_date(daily, codes):
    ds = common_dates(daily, codes)
    return ds[-1] if ds else None


# ---------------------------------------------------------------- 增量合并
def _num(x):
    if isinstance(x, bool):
        raise ValueError("布尔值不是数字")
    if isinstance(x, (int, float)):
        return float(x)
    return float(str(x).replace(",", "").strip())


def merge_inbox(inbox, universe, daily, dry_run=False):
    """把一次抓取结果并入历史库。

    inbox 结构：
      {"source": "...", "benchmark": {"000300.SH": {"2026-11-06": 4052.48}},
       "etfs": {"510300.SH": {"turnover": {"2026-11-06": 12345.0},
                              "flow": {"2026-11-06": 1.23}}}}

    turnover 单位统一为「元」，flow 单位统一为「亿元」，benchmark 为点位。
    返回 (新增/更新条数, 警告列表)。
    """
    warns, n_turn, n_flow, n_bench = [], 0, 0, 0
    known = {e["code"] for e in universe.get("etfs", [])}

    daily.setdefault("benchmark", {}).setdefault("close", {})
    daily.setdefault("etfs", {})

    bm = inbox.get("benchmark") or {}
    for code, series in bm.items():
        tgt = daily["benchmark"]["close"]
        for d, v in series.items():
            _check_date(d, warns, f"benchmark {code}")
            try:
                tgt[d] = round(_num(v), 4)
                n_bench += 1
            except Exception as e:
                warns.append(f"benchmark {code} {d}: 数值解析失败 {e}")

    for code, block in (inbox.get("etfs") or {}).items():
        if code not in known:
            warns.append(f"{code} 不在 universe.json 里，已跳过（新增标的请先改 universe.json）")
            continue
        rec = daily["etfs"].setdefault(code, {"turnover": {}, "flow": {}})
        rec.setdefault("turnover", {})
        rec.setdefault("flow", {})

        for d, v in (block.get("turnover") or {}).items():
            _check_date(d, warns, f"{code} turnover")
            try:
                x = _num(v)
            except Exception as e:
                warns.append(f"{code} {d} 成交额解析失败 {e}")
                continue
            if x <= 0:
                warns.append(f"{code} {d} 成交额为 {x}，已跳过")
                continue
            if x < 1e5:  # 明显不是「元」
                warns.append(f"{code} {d} 成交额 {x:,.0f} 疑似不是元，已按万元×1e4 修正")
                x *= 1e4
            rec["turnover"][d] = round(x, 0)
            n_turn += 1

        for d, v in (block.get("flow") or {}).items():
            _check_date(d, warns, f"{code} flow")
            try:
                x = _num(v)
            except Exception as e:
                warns.append(f"{code} {d} 净申购解析失败 {e}")
                continue
            # 单位自检：单日净申购不该超过当日成交额的 3 倍
            t = rec["turnover"].get(d)
            if t and abs(x) * 1e8 > 3 * t:
                scale = round(abs(x) * 1e8 / max(t, 1))
                warns.append(
                    f"{code} {d} 净申购 {x} 与成交额 {t/1e8:,.2f} 亿不匹配（大 {scale} 倍），"
                    f"疑似单位不是亿元，已跳过。请确认 Wind 返回口径后重试")
                continue
            rec["flow"][d] = round(x, 4)
            n_flow += 1

    if not dry_run:
        daily["updated"] = today_str()
        save("daily.json", daily)
    return dict(turnover=n_turn, flow=n_flow, benchmark=n_bench), warns


def _check_date(d, warns, tag):
    try:
        datetime.date.fromisoformat(d)
    except Exception:
        warns.append(f"{tag}: 日期 {d!r} 不是 YYYY-MM-DD，已跳过")


def save_intraday(snap):
    save("intraday.json", snap)
