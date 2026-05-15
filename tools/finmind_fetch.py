#!/usr/bin/env python3
"""
FinMind 台股配息抓取腳本(v2 — 涵蓋全市場高殖利率股票)
- 23 檔精選池(固定)
- + TWSE BWIBBU_d 全市場「殖利率 > 3%」的股票(動態擴充)
- union → 打 FinMind 拿完整配息明細
- 輸出 dividends.json 供 GitHub Pages serve

quota:約 500-700 query/day,FinMind 免費版 600/hr,單次跑約 1-2 小時內完成
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import urllib.request
import urllib.parse
import urllib.error

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockDividend"
TWSE_BWIBBU = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d"

# 23 檔精選池(對齊 lib/data/private_picks.dart,即使 TWSE 沒列也保證有)
DEFAULT_PICKS = [
    ("2884", "玉山金"), ("2885", "元大金"), ("00946", "群益科技高息成長"),
    ("00939", "統一台灣高息動能"), ("00940", "元大台灣價值高息"),
    ("5880", "合庫金"), ("2492", "華新科"), ("00918", "大華優利高填息30"),
    ("00713", "元大台灣高息低波"), ("00900", "富邦特選高股息"),
    ("00701", "國泰股利精選30"), ("2891", "中信金"), ("2880", "華南金"),
    ("1102", "亞泥"), ("2912", "統一超"), ("2412", "中華電"),
    ("2548", "華固"), ("00919", "群益台灣精選高息"),
    ("00934", "中信成長高股息"), ("00936", "台新永續高息中小"),
    ("00932", "兆豐永續高息等權"), ("00930", "永豐ESG低碳高息"),
    ("00892", "富邦台灣半導體"),
]

# 全市場掃股 — 0 = 全部抓(只要 TWSE 有列出),不過濾
# 想壓低 quota 改成 3.0 / 5.0 等,只抓高殖利率股
MIN_YIELD_INCLUDE = 0.0

START_DATE = "2024-01-01"


def fetch_twse_high_yield(min_yield: float) -> list[tuple[str, str]]:
    """從 TWSE BWIBBU_d 抓全市場股票(min_yield=0 → 全抓)"""
    req = urllib.request.Request(
        TWSE_BWIBBU,
        headers={"User-Agent": "Mozilla/5.0 jtl_dividend_navigator"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for row in data:
        code = (row.get("Code") or "").strip()
        name = (row.get("Name") or "").strip()
        if not code or not name:
            continue
        # min_yield = 0 → 全抓
        if min_yield <= 0:
            out.append((code, name))
            continue
        try:
            y = float(row.get("DividendYield") or 0)
        except ValueError:
            continue
        if y >= min_yield:
            out.append((code, name))
    return out


def fetch_finmind_one(stock_id: str, token: str, retries: int = 3) -> list:
    qs = urllib.parse.urlencode({
        "dataset": DATASET,
        "data_id": stock_id,
        "start_date": START_DATE,
        "token": token,
    })
    url = f"{FINMIND_URL}?{qs}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "jtl-dividend-navigator/2.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("status") != 200:
                msg = payload.get("msg", "")
                if "402" in str(msg) or "quota" in str(msg).lower():
                    print(f"  ⏸ {stock_id} quota 飽和,休息 60 秒", file=sys.stderr)
                    time.sleep(60)
                    continue
                return []
            return payload.get("data", []) or []
        except urllib.error.HTTPError as e:
            if e.code in (402, 429):
                print(f"  ⏸ {stock_id} HTTP {e.code} 限流,休息 60 秒", file=sys.stderr)
                time.sleep(60)
            else:
                time.sleep(5)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(5)
    return []


def normalize_event(raw: dict) -> dict | None:
    cash_ex = (raw.get("CashExDividendTradingDate") or "").strip()
    stock_ex = (raw.get("StockExDividendTradingDate") or "").strip()
    ex_date = cash_ex or stock_ex
    if not ex_date:
        return None
    cash = float(raw.get("CashEarningsDistribution") or 0) + float(raw.get("CashStatutorySurplus") or 0)
    stock = float(raw.get("StockEarningsDistribution") or 0) + float(raw.get("StockStatutorySurplus") or 0)
    if cash == 0 and stock == 0:
        return None
    return {
        "ex_date": ex_date,
        "pay_date": (raw.get("CashDividendPaymentDate") or "").strip() or None,
        "cash_dividend": round(cash, 6),
        "stock_dividend": round(stock, 6),
        "fiscal_year": str(raw.get("year") or "").replace("年", ""),
    }


def main():
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        print("❌ 環境變數 FINMIND_TOKEN 未設定", file=sys.stderr)
        sys.exit(1)

    # 第一步:TWSE 全市場掃股
    print(f"🔍 從 TWSE 抓殖利率 > {MIN_YIELD_INCLUDE}% 的股票...", file=sys.stderr)
    try:
        twse_picks = fetch_twse_high_yield(MIN_YIELD_INCLUDE)
        print(f"   TWSE 找到 {len(twse_picks)} 檔", file=sys.stderr)
    except Exception as e:
        print(f"   ⚠️ TWSE 失敗 ({e}),只跑精選池", file=sys.stderr)
        twse_picks = []

    # 第二步:union 精選池(去重)
    seen = set()
    picks: list[tuple[str, str]] = []
    for s, n in DEFAULT_PICKS:
        if s not in seen:
            seen.add(s)
            picks.append((s, n))
    for s, n in twse_picks:
        if s not in seen:
            seen.add(s)
            picks.append((s, n))
    print(f"📋 共 {len(picks)} 檔待抓 FinMind (精選 {len(DEFAULT_PICKS)} + 全市場新增 {len(picks) - len(DEFAULT_PICKS)})", file=sys.stderr)

    # 第三步:打 FinMind
    stocks = {}
    failed = []
    for i, (stock_id, name) in enumerate(picks, 1):
        if i % 20 == 0:
            print(f"   [{i}/{len(picks)}] 進度...", file=sys.stderr)
        raw_events = fetch_finmind_one(stock_id, token)
        events = [e for e in (normalize_event(r) for r in raw_events) if e]
        events.sort(key=lambda e: e["ex_date"])
        if not events:
            failed.append(stock_id)
        stocks[stock_id] = {"name": name, "events": events}
        time.sleep(0.3)  # 禮貌節流,免費版 600/hr → 約 0.17 秒/次,我們 0.3 秒

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "FinMind TaiwanStockDividend + TWSE BWIBBU_d (覆蓋全市場高殖利率股)",
        "stock_count": len(stocks),
        "covered_high_yield_threshold": MIN_YIELD_INCLUDE,
        "failed": failed,
        "stocks": stocks,
    }

    out_path = Path(os.environ.get("OUTPUT_PATH", "dividends.json"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ 寫入 {out_path}({data['stock_count']} 檔,失敗 {len(data['failed'])} 檔)", file=sys.stderr)


if __name__ == "__main__":
    main()
