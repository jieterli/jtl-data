#!/usr/bin/env python3
"""
FinMind 台股配息抓取腳本
- 讀 picks.txt(每行一個 stock_id)或 fallback hard-coded list
- 打 FinMind TaiwanStockDividend
- 輸出 dividends.json 供 GitHub Pages serve
- token 從 FINMIND_TOKEN env var 讀取(GitHub Actions secret)
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

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockDividend"

# 預設 picks(對齊 lib/data/private_picks.dart 的 18 檔精選池)
DEFAULT_PICKS = [
    ("2884", "玉山金"),
    ("2885", "元大金"),
    ("00946", "群益科技高息成長"),
    ("00939", "統一台灣高息動能"),
    ("00940", "元大台灣價值高息"),
    ("5880", "合庫金"),
    ("2492", "華新科"),
    ("00918", "大華優利高填息30"),
    ("00713", "元大台灣高息低波"),
    ("00900", "富邦特選高股息"),
    ("00701", "國泰股利精選30"),
    ("2891", "中信金"),
    ("2880", "華南金"),
    ("1102", "亞泥"),
    ("2912", "統一超"),
    ("2412", "中華電"),
    ("2548", "華固"),
    ("00919", "群益台灣精選高息"),
    ("00934", "中信成長高股息"),
    ("00936", "台新永續高息中小"),
    ("00932", "兆豐永續高息等權"),
    ("00930", "永豐ESG低碳高息"),
    ("00892", "富邦台灣半導體"),
]

START_DATE = "2024-01-01"  # 抓近一年半,夠涵蓋季配/月配 ETF 完整循環


def fetch_one(stock_id: str, token: str, retries: int = 3) -> list:
    qs = urllib.parse.urlencode({
        "dataset": DATASET,
        "data_id": stock_id,
        "start_date": START_DATE,
        "token": token,
    })
    url = f"{API_URL}?{qs}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jtl-dividend-navigator/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("status") != 200:
                print(f"  ⚠️ {stock_id} status={payload.get('status')} msg={payload.get('msg')}", file=sys.stderr)
                return []
            return payload.get("data", []) or []
        except urllib.error.HTTPError as e:
            print(f"  ⚠️ {stock_id} HTTPError {e.code} (attempt {attempt+1}/{retries})", file=sys.stderr)
            if e.code == 402 or e.code == 429:
                time.sleep(60)  # quota / rate limit → 等久一點
            else:
                time.sleep(5)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  ⚠️ {stock_id} {type(e).__name__}: {e} (attempt {attempt+1}/{retries})", file=sys.stderr)
            time.sleep(5)
    print(f"  ❌ {stock_id} 全部 retry 失敗,跳過", file=sys.stderr)
    return []


def normalize_event(raw: dict) -> dict | None:
    """把 FinMind 原始 row 轉成 APP 需要的精簡格式。除息日是 key — 沒除息日的整筆丟掉。"""
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


def build_dataset(token: str, picks: list[tuple[str, str]]) -> dict:
    stocks = {}
    failed = []
    for i, (stock_id, name) in enumerate(picks, 1):
        print(f"[{i}/{len(picks)}] {stock_id} {name}", file=sys.stderr)
        raw_events = fetch_one(stock_id, token)
        events = [e for e in (normalize_event(r) for r in raw_events) if e]
        # 排序 ex_date ASC
        events.sort(key=lambda e: e["ex_date"])
        if not events:
            failed.append(stock_id)
        stocks[stock_id] = {
            "name": name,
            "events": events,
        }
        time.sleep(0.5)  # 友善節流,免費版 600/hr 綽綽有餘但禮貌一下
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "FinMind TaiwanStockDividend",
        "stock_count": len(stocks),
        "failed": failed,
        "stocks": stocks,
    }


def main():
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        print("❌ 環境變數 FINMIND_TOKEN 未設定", file=sys.stderr)
        sys.exit(1)

    # 可選:從 picks.txt 讀(stock_id<TAB>name 一行一筆)
    picks_file = Path(__file__).with_name("picks.txt")
    if picks_file.exists():
        picks = []
        for line in picks_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t") if "\t" in line else line.split(",", 1)
            sid = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else sid
            picks.append((sid, name))
        print(f"從 picks.txt 讀到 {len(picks)} 檔", file=sys.stderr)
    else:
        picks = DEFAULT_PICKS
        print(f"使用內建預設 {len(picks)} 檔", file=sys.stderr)

    data = build_dataset(token, picks)

    out_path = Path(os.environ.get("OUTPUT_PATH", "dividends.json"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ 寫入 {out_path}({data['stock_count']} 檔,失敗 {len(data['failed'])} 檔)", file=sys.stderr)


if __name__ == "__main__":
    main()
