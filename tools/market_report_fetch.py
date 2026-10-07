#!/usr/bin/env python3
"""台股盤後報告 — 抓官方公開資料 → report/YYYY-MM-DD.json + report/latest.json

⚠️ 合法資料源白名單(2026-10-06 查證,胡老師拍板「新功能全走合法源」):
   ✅ openapi.twse.com.tw      證交所開放 API,政府資料開放授權(https://data.gov.tw/license)
   ✅ api.finmindtrade.com     FinMind(股息婆婆已在用)

⏱ 時效(2026-10-07 實測,收盤九小時後):
   證交所開放 API 的 MI_INDEX / FMTQIK / STOCK_DAY_ALL **當天不會出,隔天才補**。
   FinMind 當天就有。所以大盤、個股、法人以 FinMind 為主、證交所為輔(兩邊都有就對帳),
   類股指數只有證交所有 → 會比大盤慢一天,照實標它自己的 asOf,畫面要標明。
   ❌ www.twse.com.tw/rwd/...  **不可用** —— 證交所使用條款「下載軟體或資料」一節明文
      禁止以自動化裝置、指令碼、爬蟲程式下載本網站資料,僅「已授權政府資料開放平臺」
      的資料不在此限(https://www.twse.com.tw/zh/page/terms/use.html)。
      既有的 chips_fetch.py 有用到 rwd,那是另一件待辦,本腳本不碰。

硬規則(來自交接文件「開工前必讀與第一原則」):
   1. 每個數字都要能追溯到官方欄位,或由官方欄位用程式算出來。
   2. 資料源沒有就標 pending / unavailable,**不准**用昨天的值、平均值、推估值補。
   3. 已經是 ok 的欄位,後面的排程**不准**被空值覆蓋。
   4. 判讀 / 建議一律不寫在這裡。這支只輸出數字(事實層)。
      辦公室 APP 的判讀層是另一份 latest_pro.json,之後才做。

用法:
   python tools/market_report_fetch.py            # 寫檔
   python tools/market_report_fetch.py --dry-run  # 只印出來不寫檔
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))

TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# 用到的端點(欄位說明見 repo 根目錄 CLAUDE.md)
EP_MI_INDEX = f"{TWSE_OPENAPI}/exchangeReport/MI_INDEX"          # 各指數收盤、漲跌點數、漲跌百分比
EP_TAIEX_HIST = f"{TWSE_OPENAPI}/indicesReport/MI_5MINS_HIST"    # 加權指數開高低收
EP_FMTQIK = f"{TWSE_OPENAPI}/exchangeReport/FMTQIK"              # 市場成交量 / 值 / 筆數
EP_STOCK_DAY_ALL = f"{TWSE_OPENAPI}/exchangeReport/STOCK_DAY_ALL"  # 上市個股日成交
EP_ADVANCE_DECLINE = f"{TWSE_OPENAPI}/opendata/twtazu_od"        # 漲跌證券數(⚠ 開放版更新不穩)
EP_HOLIDAY = f"{TWSE_OPENAPI}/holidaySchedule/holidaySchedule"   # 開(休)市日期

FM_PRICE = "TaiwanStockPrice"          # 日 K(data_id=TAIEX 就是加權指數)
FM_INSTI = "TaiwanStockTotalInstitutionalInvestors"  # 三大法人買賣金額

MAIN_INDEX_NAME = "發行量加權股價指數"

# 證交所的 37 個「類指數」裡有 5 個是**大類**,會把子類再加總一次,
# 跟子類一起排名就是重複計算(2026-10-07 修:原本只排除 3 個,漏了化學生技醫療與電子工業
#  → 排行榜出現「電子工業」跟它底下的「半導體、電子零組件」同時進榜)。
#   水泥窯製類     = 水泥 + 玻璃陶瓷
#   塑膠化工類     = 塑膠 + 化學生技醫療
#   化學生技醫療類 = 化學 + 生技醫療
#   機電類         = 電機機械 + 電器電纜 + 電子工業
#   電子工業類     = 半導體 + 電腦及週邊 + 光電 + 通信網路 + 電子零組件 + 電子通路 + 資訊服務 + 其他電子
# 排行榜只取子類;大類的原始數字照樣留在 sectors.all(aggregate=true)供核對。
AGGREGATE_SECTORS = {
    "水泥窯製類指數",
    "塑膠化工類指數",
    "化學生技醫療類指數",
    "機電類指數",
    "電子工業類指數",
}

# 權值股卡片要顯示的個股(交接文件只點名台積電;要加再往這裡加)
HEAVYWEIGHTS = {"2330": "台積電"}

# FinMind 的單位名 → 我們的欄位名 / 官方表上的中文名
INSTI_MAP = {
    "Dealer_self": ("dealerSelf", "自營商(自行買賣)"),
    "Dealer_Hedging": ("dealerHedging", "自營商(避險)"),
    "Investment_Trust": ("investmentTrust", "投信"),
    "Foreign_Investor": ("foreign", "外資及陸資(不含外資自營商)"),
    "Foreign_Dealer_Self": ("foreignDealer", "外資自營商"),
    "total": ("total", "合計"),
}

OK, PENDING, UNAVAILABLE, ERROR = "ok", "pending", "unavailable", "error"


# ─────────────────────────────────────────────────────────────
# 純函式(有單元測試:tools/test_market_report.py)
# ─────────────────────────────────────────────────────────────

def roc_to_iso(roc: str) -> str | None:
    """民國日期 '1151005' → '2026-10-05'。格式不對回 None。

    跟辦公室 APP 的 MarketCalendar.rocDateToKey 同一套算法。
    """
    roc = (roc or "").strip()
    if len(roc) < 7 or not roc.isdigit():
        return None
    y, m, d = roc[:-4], roc[-4:-2], roc[-2:]
    try:
        year = int(y) + 1911
        month, day = int(m), int(d)
        datetime(year, month, day)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def to_scaled(text, scale: int):
    """官方字串 → 整數原值(乘 10**scale)。無法解析回 None。

    官方表的缺值長相很多:'--'、''、'X'、'0.00X'、千分位逗號、開頭 '+'。
    解析不出來一律回 None(= 不是 0,0 是真的有成交但沒變動)。
    """
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("+", "").replace(" ", "")
    if s in ("", "--", "---", "X", "x", "null", "None"):
        return None
    try:
        return int(round(float(s) * (10 ** scale)))
    except ValueError:
        return None


def field(value, scale: int, status: str, source: str, as_of: str | None, note: str | None = None) -> dict:
    """一個數字 = 值 + 狀態 + 來源 + 資料日期。APP 依 status 決定怎麼顯示。"""
    out = {"value": value, "scale": scale, "status": status, "source": source, "asOf": as_of}
    if note:
        out["note"] = note
    return out


def _dated(value, scale: int, source: str, row_date: str | None, trade_date: str,
           note: str | None = None) -> dict:
    """值 + 日期驗證。日期不符 trade_date 或值解析不出來 → pending,不是 ok。"""
    if value is None:
        return field(None, scale, PENDING, source, row_date, note)
    if row_date != trade_date:
        return field(None, scale, PENDING, source, row_date,
                     note or f"資料源日期 {row_date} 不是 {trade_date}")
    return field(value, scale, OK, source, row_date, note)


def pick_latest(rows: list[dict], date_key: str) -> tuple[str | None, dict | None]:
    """挑出資料裡最新那天的那一列。回 (iso 日期, 該列)。"""
    best_iso, best_row = None, None
    for r in rows or []:
        iso = roc_to_iso(str(r.get(date_key, "")))
        if iso and (best_iso is None or iso > best_iso):
            best_iso, best_row = iso, r
    return best_iso, best_row


def build_taiex(mi_rows, hist_rows, fmtqik_rows, trade_date: str, fm_rows=None) -> dict:
    """大盤卡片:收盤、漲跌點、漲跌幅、開高低、成交量值筆數。

    FinMind 優先(當天就有),證交所開放 API 補位並對帳(隔天才有)。
    兩邊同一天卻對不起來 → 標 error,**寧可不顯示也不顯示錯的**。
    """
    fm = None
    for r in fm_rows or []:
        if str(r.get("date", "")) == trade_date:
            fm = r
    prev_close = None
    if fm is not None:
        earlier = [r for r in (fm_rows or []) if str(r.get("date", "")) < trade_date]
        if earlier:
            earlier.sort(key=lambda r: r["date"])
            prev_close = earlier[-1].get("close")

    # 證交所那邊(可能還是前一天的)
    tw, tw_date = None, None
    for r in mi_rows or []:
        if str(r.get("指數", "")).strip() == MAIN_INDEX_NAME:
            iso = roc_to_iso(str(r.get("日期", "")))
            if iso and (tw_date is None or iso > tw_date):
                tw, tw_date = r, iso

    out = {}

    def from_finmind(key, scale):
        v = to_scaled(fm.get(key), scale) if fm else None
        return None if v is None else field(v, scale, OK, "FinMind", trade_date)

    if fm is not None:
        out["close"] = from_finmind("close", 2)
        out["open"] = from_finmind("open", 2)
        out["high"] = from_finmind("max", 2)
        out["low"] = from_finmind("min", 2)
        out["tradeValue"] = from_finmind("Trading_money", 0)
        out["tradeVolume"] = from_finmind("Trading_Volume", 0)
        out["transaction"] = from_finmind("Trading_turnover", 0)
        spread = to_scaled(fm.get("spread"), 2)
        out["change"] = (field(spread, 2, OK, "FinMind", trade_date)
                         if spread is not None
                         else field(None, 2, PENDING, "FinMind", trade_date))
        if spread is not None and prev_close:
            pct = spread / 100 / float(prev_close) * 100
            out["changePct"] = field(round(pct * 100), 2, OK, "FinMind", trade_date)
        else:
            out["changePct"] = field(None, 2, PENDING, "FinMind", trade_date)

        # 對帳:證交所也出了同一天就比收盤,差超過 1 點代表有一邊不對
        if tw is not None and tw_date == trade_date:
            tw_close = to_scaled(tw.get("收盤指數"), 2)
            if tw_close is not None and out["close"]["value"] is not None:
                if abs(tw_close - out["close"]["value"]) > 100:  # scale 2 → 100 = 1 點
                    out["close"] = field(
                        None, 2, ERROR, "FinMind+TWSE", trade_date,
                        f"兩個來源對不起來:FinMind {out['close']['value'] / 100}"
                        f" vs 證交所 {tw_close / 100}")
        return out

    # FinMind 沒有這天 → 退回證交所開放 API
    src = "TWSE"
    if tw:
        sign = -1 if str(tw.get("漲跌", "")).strip() == "-" else 1
        pts = to_scaled(tw.get("漲跌點數"), 2)
        pct = to_scaled(tw.get("漲跌百分比"), 2)
        out["close"] = _dated(to_scaled(tw.get("收盤指數"), 2), 2, src, tw_date, trade_date)
        out["change"] = _dated(None if pts is None else sign * pts, 2, src, tw_date, trade_date)
        out["changePct"] = _dated(None if pct is None else sign * abs(pct), 2, src, tw_date, trade_date)
    else:
        for k in ("close", "change", "changePct"):
            out[k] = field(None, 2, PENDING, src, None, "MI_INDEX 查無發行量加權股價指數")

    hist_date, hist = pick_latest(hist_rows, "Date")
    for key, api_key in (("open", "OpeningIndex"), ("high", "HighestIndex"), ("low", "LowestIndex")):
        out[key] = _dated(to_scaled((hist or {}).get(api_key), 2), 2, src, hist_date, trade_date)

    q_date, q = pick_latest(fmtqik_rows, "Date")
    out["tradeValue"] = _dated(to_scaled((q or {}).get("TradeValue"), 0), 0, src, q_date, trade_date)
    out["tradeVolume"] = _dated(to_scaled((q or {}).get("TradeVolume"), 0), 0, src, q_date, trade_date)
    out["transaction"] = _dated(to_scaled((q or {}).get("Transaction"), 0), 0, src, q_date, trade_date)
    return out

def build_advance_decline(rows, trade_date: str) -> dict:
    """漲跌家數。開放版 twtazu_od 實測會停在舊日期(2026-10-06 查到的是 6/5),
    日期不符就標 unavailable —— 畫面顯示「本日未提供」,**不准**拿舊數字充當今天。
    哪天證交所把開放版修好,這裡會自動變 ok。"""
    src = "TWSE"
    stock_row, row_date = None, None
    for r in rows or []:
        if str(r.get("類型", "")).strip() == "股票":
            iso = roc_to_iso(str(r.get("出表日期", "")))
            if iso and (row_date is None or iso > row_date):
                stock_row, row_date = r, iso

    def cnt(key):
        if stock_row is None:
            return field(None, 0, UNAVAILABLE, src, None, "開放版未提供")
        v = to_scaled(str(stock_row.get(key, "")).split("(")[0], 0)
        if row_date != trade_date:
            return field(None, 0, UNAVAILABLE, src, row_date,
                         f"開放版停在 {row_date},非 {trade_date}")
        return field(v, 0, OK if v is not None else UNAVAILABLE, src, row_date)

    return {"advances": cnt("上漲"), "declines": cnt("下跌"), "unchanged": cnt("持平")}


def build_sectors(mi_rows, trade_date: str) -> dict:
    """類股指數。all = 全部類股(含大類,標 aggregate);排行榜只取非大類。"""
    src = "TWSE"
    items = []
    for r in mi_rows or []:
        name = str(r.get("指數", "")).strip()
        if not name.endswith("類指數"):
            continue
        iso = roc_to_iso(str(r.get("日期", "")))
        sign = -1 if str(r.get("漲跌", "")).strip() == "-" else 1
        pct = to_scaled(r.get("漲跌百分比"), 2)
        close = to_scaled(r.get("收盤指數"), 2)
        # ⚠️ 類股只有證交所有,而證交所開放 API 比 FinMind 慢一天。
        # 這裡**不**套「日期不符就 pending」,否則整個類股區會空掉;
        # 改成照實記自己的 asOf,畫面再標明「類股資料日期 X/X」。
        items.append({
            "name": name,
            "aggregate": name in AGGREGATE_SECTORS,
            "close": field(close, 2, OK if close is not None else PENDING, src, iso),
            "changePct": field(None if pct is None else sign * abs(pct), 2,
                               OK if pct is not None else PENDING, src, iso),
        })

    ranked = [i for i in items
              if not i["aggregate"] and i["changePct"]["status"] == OK]
    ranked.sort(key=lambda i: i["changePct"]["value"], reverse=True)
    sector_date = items[0]["close"]["asOf"] if items else None
    return {
        "all": items,
        "topGainers": [i["name"] for i in ranked[:4]],
        "topLosers": [i["name"] for i in reversed(ranked[-3:])] if len(ranked) >= 3 else [],
        "status": OK if ranked else PENDING,
        "source": src,
        "asOf": sector_date,
        # 類股比大盤慢一天時,畫面要標出來(別讓人以為是當天的)
        "staleVsTradeDate": bool(sector_date and sector_date != trade_date),
    }


def build_heavyweights(stock_rows, trade_date: str, fm_rows_by_code=None) -> dict:
    """權值股(台積電)收盤與漲跌。Change 官方給的是絕對值帶正負號。"""
    src = "TWSE"
    by_code = {}
    for r in stock_rows or []:
        code = str(r.get("Code", "")).strip()
        if code in HEAVYWEIGHTS:
            by_code[code] = r

    out = {}
    for code, name in HEAVYWEIGHTS.items():
        # FinMind 當天就有,證交所要隔天 → FinMind 優先
        fm = ((fm_rows_by_code or {}).get(code) or {})
        if str(fm.get("date", "")) == trade_date:
            out[code] = {
                "name": name,
                "close": field(to_scaled(fm.get("close"), 2), 2, OK, "FinMind", trade_date),
                "change": field(to_scaled(fm.get("spread"), 2), 2, OK, "FinMind", trade_date),
                "open": field(to_scaled(fm.get("open"), 2), 2, OK, "FinMind", trade_date),
                "high": field(to_scaled(fm.get("max"), 2), 2, OK, "FinMind", trade_date),
                "low": field(to_scaled(fm.get("min"), 2), 2, OK, "FinMind", trade_date),
                "tradeValue": field(to_scaled(fm.get("Trading_money"), 0), 0, OK, "FinMind", trade_date),
            }
            continue
        r = by_code.get(code)
        iso = roc_to_iso(str((r or {}).get("Date", ""))) if r else None
        out[code] = {
            "name": name,
            "close": _dated(to_scaled((r or {}).get("ClosingPrice"), 2), 2, src, iso, trade_date),
            "change": _dated(to_scaled((r or {}).get("Change"), 2), 2, src, iso, trade_date),
            "open": _dated(to_scaled((r or {}).get("OpeningPrice"), 2), 2, src, iso, trade_date),
            "high": _dated(to_scaled((r or {}).get("HighestPrice"), 2), 2, src, iso, trade_date),
            "low": _dated(to_scaled((r or {}).get("LowestPrice"), 2), 2, src, iso, trade_date),
            "tradeValue": _dated(to_scaled((r or {}).get("TradeValue"), 0), 0, src, iso, trade_date),
        }
    return out


def build_institutional(fm_rows, trade_date: str) -> dict:
    """三大法人買賣金額(FinMind,實測與證交所 BFI82U 數字完全一致)。
    外資含不含外資自營商照官方分法分開列,**不自行合併**。"""
    src = "FinMind"
    out = {}
    seen = {}
    for r in fm_rows or []:
        nm = str(r.get("name", ""))
        if nm in INSTI_MAP:
            seen[nm] = r

    for fm_name, (key, zh) in INSTI_MAP.items():
        r = seen.get(fm_name)
        row_date = str((r or {}).get("date", "")) or None
        buy = to_scaled((r or {}).get("buy"), 0)
        sell = to_scaled((r or {}).get("sell"), 0)
        net = None if (buy is None or sell is None) else buy - sell
        out[key] = {
            "label": zh,
            "buy": _dated(buy, 0, src, row_date, trade_date),
            "sell": _dated(sell, 0, src, row_date, trade_date),
            "net": _dated(net, 0, src, row_date, trade_date),
        }
    return out


def build_holidays(holiday_rows, today_iso: str, limit: int = 6) -> dict:
    """近期休市日。名稱含「交易日」的是交易日公告,不是休市 —— 要略過
    (跟辦公室 APP 的 MarketCalendar.parseHolidays 同一套規則)。"""
    src = "TWSE"
    items = []
    for r in holiday_rows or []:
        name = str(r.get("Name", "")).strip()
        if not name or "交易日" in name:
            continue
        iso = roc_to_iso(str(r.get("Date", "")))
        if iso and iso >= today_iso:
            items.append({"date": iso, "name": name,
                          "weekday": str(r.get("Weekday", "")).strip()})
    items.sort(key=lambda i: i["date"])
    return {
        "items": items[:limit],
        "status": OK if items else UNAVAILABLE,
        "source": src,
        "asOf": today_iso if items else None,
    }


def merge_keep_ok(old, new):
    """已經是 ok 的欄位不准被非 ok 覆蓋(排程跑第二、三次時最重要)。

    其他情況一律以 new 為準 —— 官方更正數字時要蓋得掉。
    """
    if isinstance(old, dict) and isinstance(new, dict):
        if "status" in old and "status" in new and "value" in old and "value" in new:
            return dict(old) if (old.get("status") == OK and new.get("status") != OK) else dict(new)
        out = dict(new)
        for k, v in old.items():
            out[k] = merge_keep_ok(v, new[k]) if k in new else v
        return out
    return new


# ─────────────────────────────────────────────────────────────
# I/O
# ─────────────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = 30, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jtl-data/market-report"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — 每項獨立,一項失敗不影響其他項
            last = exc
    print(f"⚠️  抓取失敗 {url}: {last}", file=sys.stderr)
    return None


# 台股 13:30 收盤,留一小時給資料落地。盤中跑排程時,當天那根是「還沒收盤」的半根,
# 拿去當收盤價就是在發錯資訊 —— 一律擋掉(2026-10-07 加)。
MARKET_SETTLED_HOUR = 14.5


def usable_finmind_rows(rows, now: datetime):
    """砍掉「今天但還沒收盤」的那一根。其他日期照留。"""
    today = now.date().isoformat()
    settled = (now.hour + now.minute / 60) >= MARKET_SETTLED_HOUR
    out = []
    for r in rows or []:
        d = str(r.get("date", ""))
        if d == today and not settled:
            continue
        if d > today:
            continue
        out.append(r)
    return out


def fetch_finmind_price(data_id: str, start_date: str):
    params = {
        "dataset": FM_PRICE,
        "data_id": data_id,
        "start_date": start_date,
    }
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if token:
        params["token"] = token
    payload = fetch_json(f"{FINMIND_API}?{urllib.parse.urlencode(params)}")
    if not payload or payload.get("status") != 200:
        return []
    return payload.get("data") or []


def fetch_institutional_rows(trade_date: str):
    params = {
        "dataset": FM_INSTI,
        "start_date": trade_date,
        "end_date": trade_date,
    }
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if token:
        params["token"] = token
    payload = fetch_json(f"{FINMIND_API}?{urllib.parse.urlencode(params)}")
    if not payload or payload.get("status") != 200:
        return None
    return payload.get("data") or []


def build_report(now: datetime | None = None) -> dict:
    now = now or datetime.now(TPE)
    today_iso = now.date().isoformat()

    mi_rows = fetch_json(EP_MI_INDEX)
    hist_rows = fetch_json(EP_TAIEX_HIST)
    fmtqik_rows = fetch_json(EP_FMTQIK)
    stock_rows = fetch_json(EP_STOCK_DAY_ALL)
    adv_rows = fetch_json(EP_ADVANCE_DECLINE)
    holiday_rows = fetch_json(EP_HOLIDAY)

    # FinMind:當天收盤後就有,證交所開放 API 要隔天(2026-10-07 實測)
    start = (now - timedelta(days=20)).date().isoformat()
    fm_taiex = usable_finmind_rows(fetch_finmind_price("TAIEX", start), now)
    fm_heavy = {
        code: usable_finmind_rows(fetch_finmind_price(code, start), now)
        for code in HEAVYWEIGHTS
    }

    # 交易日 = 兩邊有資料的最新那天(誰先出就用誰,不等對方)
    candidates = []
    if fm_taiex:
        candidates.append(max(str(r.get("date", "")) for r in fm_taiex))
    twse_date, _ = pick_latest(
        [r for r in (mi_rows or []) if str(r.get("指數", "")).strip() == MAIN_INDEX_NAME],
        "日期",
    )
    if twse_date:
        candidates.append(twse_date)
    trade_date = max(candidates) if candidates else today_iso

    fm_heavy_latest = {}
    for code, rows in fm_heavy.items():
        for r in rows:
            if str(r.get("date", "")) == trade_date:
                fm_heavy_latest[code] = r

    insti_rows = fetch_institutional_rows(trade_date)

    return {
        "tradeDate": trade_date,
        "generatedAt": now.isoformat(timespec="seconds"),
        "isLatestTradingDayToday": trade_date == today_iso,
        "taiex": build_taiex(mi_rows, hist_rows, fmtqik_rows, trade_date, fm_rows=fm_taiex),
        "breadth": build_advance_decline(adv_rows, trade_date),
        "heavyweights": build_heavyweights(stock_rows, trade_date, fm_rows_by_code=fm_heavy_latest),
        "sectors": build_sectors(mi_rows, trade_date),
        "institutional": build_institutional(insti_rows, trade_date),
        "holidays": build_holidays(holiday_rows, today_iso),
        "attribution": "資料來源:臺灣證券交易所(開放資料)、FinMind",
        "disclaimer": "本頁僅彙整臺灣證券交易所公開資料,不提供任何投資建議。",
    }


def write_report(report: dict, repo_root: str) -> list[str]:
    out_dir = os.path.join(repo_root, "report")
    os.makedirs(out_dir, exist_ok=True)
    dated_path = os.path.join(out_dir, f"{report['tradeDate']}.json")

    if os.path.exists(dated_path):
        with open(dated_path, encoding="utf-8") as fh:
            try:
                report = merge_keep_ok(json.load(fh), report)
            except json.JSONDecodeError:
                pass  # 檔壞掉就以新的為準

    written = []
    for path in (dated_path, os.path.join(out_dir, "latest.json")):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=False)
            fh.write("\n")
        written.append(path)
    return written


def summarize(report: dict) -> str:
    def val(f, unit=""):
        if not isinstance(f, dict) or f.get("status") != OK or f.get("value") is None:
            return {"pending": "待公告", "unavailable": "本日未提供"}.get(
                (f or {}).get("status"), "資料暫時無法取得")
        return f"{f['value'] / (10 ** f.get('scale', 0)):,.2f}{unit}"

    t = report["taiex"]
    lines = [
        f"資料日期 {report['tradeDate']}(今天是最新交易日:{report['isLatestTradingDayToday']})",
        f"加權指數 {val(t['close'])}  漲跌 {val(t['change'])}  ({val(t['changePct'], '%')})",
        f"開 {val(t['open'])} 高 {val(t['high'])} 低 {val(t['low'])}",
        f"成交值 {val(t['tradeValue'])} 元",
        f"漲跌家數 上漲 {val(report['breadth']['advances'])} / 下跌 {val(report['breadth']['declines'])}"
        f" / 持平 {val(report['breadth']['unchanged'])}",
        f"台積電 {val(report['heavyweights']['2330']['close'])}  漲跌 {val(report['heavyweights']['2330']['change'])}",
        f"類股漲幅前四 {report['sectors']['topGainers']}",
        f"類股跌幅前三 {report['sectors']['topLosers']}",
        f"三大法人合計買賣差額 {val(report['institutional']['total']['net'])} 元",
        f"近期休市 {[(h['date'], h['name']) for h in report['holidays']['items'][:3]]}",
    ]
    return "\n".join(lines)


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report = build_report()
    print(summarize(report))
    if "--dry-run" in sys.argv:
        print("\n(--dry-run:沒有寫檔)")
        return 0
    for path in write_report(report, repo_root):
        print(f"✅ 寫入 {os.path.relpath(path, repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
