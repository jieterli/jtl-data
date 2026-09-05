#!/usr/bin/env python3
"""
籌碼日報抓取(2026-09-05 為「股息管家婆婆 → 婆婆幫你整理」建的)。
全部走 TWSE / TPEx **免費公開日報**,一天幾個 request 就拿到全市場,不吃 FinMind 額度、不用 token。

輸出:chips/<代號>.json(每檔一個檔,保留最近 KEEP_DAYS 個交易日)
      chips/index.json(更新時間、檔數)
每檔格式(days 一天一行,git diff 才小):
  {"symbol":"2330","name":"台積電","market":"TWSE","updated":"2026-09-05","fields":[...],
   "days":[["2026-09-04",close,change,volume,foreign_net,trust_net,dealer_net,total_net,margin_bal,short_bal,foreign_pct,high,low], ...]}
  股數單位:股;餘額單位:張;foreign_pct:%;change:元(相對前一日)。缺值 = null。

上市(TWSE)可帶日期回補歷史;上櫃(TPEx)OpenAPI 只有當日 → 從第一次跑的那天起累積。
"""
import json
import os
import re
import sys
import time
import re as _re
import ssl
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120 Safari/537.36"
OUT_DIR = Path(os.environ.get("CHIPS_DIR", "chips"))
KEEP_DAYS = 60
# 一次最多回補幾個交易日(GitHub Actions 有 6 小時上限;TWSE 每次請求間隔 SLEEP 秒)
MAX_BACKFILL = int(os.environ.get("MAX_BACKFILL", "45"))
SLEEP = float(os.environ.get("FETCH_SLEEP", "2.5"))
FIELDS = ["date", "close", "change", "volume", "foreign_net", "trust_net", "dealer_net",
          "total_net", "margin_bal", "short_bal", "foreign_pct", "high", "low"]

TWSE = {
    "quotes": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d8}&type=ALLBUT0999&response=json",
    "insti": "https://www.twse.com.tw/rwd/zh/fund/T86?date={d8}&selectType=ALLBUT0999&response=json",
    "qfii": "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date={d8}&selectType=ALLBUT0999&response=json",
    "margin": "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d8}&selectType=ALL&response=json",
}
TPEX = {
    "quotes": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    "insti": "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading",
    "qfii": "https://www.tpex.org.tw/openapi/v1/tpex_3insti_qfii",
    "margin": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance",
}


def log(msg):
    print(msg, flush=True)


_SSL_LAX = ssl.create_default_context()
_SSL_LAX.check_hostname = False
_SSL_LAX.verify_mode = ssl.CERT_NONE


def get_json(url, tries=3):
    ctx = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
                body = r.read().decode("utf-8", "replace")
            return json.loads(body)
        except Exception as e:  # noqa: BLE001
            log(f"  ! {url[:80]} … {e} (try {i + 1})")
            # 櫃買中心憑證缺 Subject Key Identifier,macOS 新版 Python 會驗不過(Ubuntu 沒事);
            # 公開日報不是敏感資料,退一步用不驗證的 context 重試。
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                ctx = _SSL_LAX
            time.sleep(3 * (i + 1))
    return None


# 只留「股票 / ETF」:4 碼股票(可帶一個英文字尾)、00 開頭 ETF;排除權證(6 碼 7 開頭等)
_STOCK_CODE = _re.compile(r"^(\d{4}[A-Z]?|00\d{2,4}[A-Z]?)$")


def is_stock_code(code):
    return bool(_STOCK_CODE.match(str(code).strip()))


def num(s):
    """'1,234' / '+0.33' / '87.85%' / '' → float | None"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(",", "").replace("%", "")
    t = re.sub(r"<[^>]+>", "", t)
    if t in ("", "-", "--", "X", "除權", "除息", "除權息"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def roc_to_iso(s):
    """'1150904' → '2026-09-04'"""
    s = str(s).strip()
    if len(s) == 7 and s.isdigit():
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:7]}"
    return s


def clean_code(s):
    return str(s).strip()


# ── 儲存層 ───────────────────────────────────────────────
def load_stock(sym):
    p = OUT_DIR / f"{sym}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def dump_stock(rec):
    """days 一天一行,其餘壓一行"""
    head = {k: rec[k] for k in ("symbol", "name", "market", "updated", "fields")}
    lines = [json.dumps(d, ensure_ascii=False, separators=(",", ":")) for d in rec["days"]]
    body = json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1]
    return body + ',"days":[\n' + ",\n".join(lines) + "\n]}\n"


class Store:
    def __init__(self):
        self.recs = {}  # sym → rec
        self.dirty = set()
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for p in OUT_DIR.glob("*.json"):
            if p.name == "index.json":
                continue
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                self.recs[r["symbol"]] = r
            except Exception:  # noqa: BLE001
                pass
        log(f"loaded {len(self.recs)} stock files")

    def day_map(self, rec):
        return {d[0]: d for d in rec["days"]}

    def upsert(self, market, sym, name, iso, values):
        """values: dict 欄位 → 數字;同一天多來源會合併"""
        if not is_stock_code(sym):
            return
        rec = self.recs.get(sym)
        if rec is None:
            rec = {"symbol": sym, "name": name, "market": market, "updated": "", "fields": FIELDS, "days": []}
            self.recs[sym] = rec
        if name and rec.get("name") != name:
            rec["name"] = name
        dm = self.day_map(rec)
        row = dm.get(iso)
        if row is None:
            row = [iso] + [None] * (len(FIELDS) - 1)
            rec["days"].append(row)
        for k, v in values.items():
            idx = FIELDS.index(k)
            if v is not None:
                row[idx] = v
        self.dirty.add(sym)

    def has_day(self, market, iso, field="close"):
        """這一天這個市場有沒有抓過(任一檔該欄位有值就算)。
        TPEx 用 total_net 判:行情抓到但法人那段失敗時,下次還會補。"""
        idx = FIELDS.index(field)
        for r in self.recs.values():
            if r["market"] == market and any(d[0] == iso and d[idx] is not None for d in r["days"]):
                return True
        return False

    def save(self, today_iso):
        n = 0
        for sym in self.dirty:
            rec = self.recs[sym]
            rec["days"].sort(key=lambda d: d[0])
            rec["days"] = rec["days"][-KEEP_DAYS:]
            rec["updated"] = today_iso
            (OUT_DIR / f"{sym}.json").write_text(dump_stock(rec), encoding="utf-8")
            n += 1
        idx = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "TWSE(MI_INDEX/T86/MI_QFIIS/MI_MARGN) + TPEx OpenAPI",
            "stock_count": len(self.recs),
            "fields": FIELDS,
            "keep_days": KEEP_DAYS,
            "twse_dates": sorted({d[0] for r in self.recs.values() if r["market"] == "TWSE" for d in r["days"]})[-KEEP_DAYS:],
            "tpex_dates": sorted({d[0] for r in self.recs.values() if r["market"] == "TPEx" for d in r["days"]})[-KEEP_DAYS:],
        }
        (OUT_DIR / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"saved {n} files, total {len(self.recs)}")


# ── TWSE(帶日期)────────────────────────────────────────
def fetch_twse_day(store, d: date):
    d8 = d.strftime("%Y%m%d")
    iso = d.isoformat()
    q = get_json(TWSE["quotes"].format(d8=d8))
    time.sleep(SLEEP)
    if not q or q.get("stat") != "OK":
        log(f"TWSE {iso}: 非交易日或無資料")
        return False
    tables = q.get("tables") or []
    qt = next((t for t in tables if "每日收盤行情" in (t.get("title") or "")), None)
    if not qt:
        log(f"TWSE {iso}: 找不到收盤行情表")
        return False
    f = qt["fields"]
    ic, iname, ivol, iclose, isign, idiff, ihigh, ilow = (
        f.index("證券代號"), f.index("證券名稱"), f.index("成交股數"), f.index("收盤價"),
        f.index("漲跌(+/-)"), f.index("漲跌價差"), f.index("最高價"), f.index("最低價"))
    n = 0
    for r in qt["data"]:
        sym = clean_code(r[ic])
        close = num(r[iclose])
        if close is None or not is_stock_code(sym):
            continue
        sign = -1.0 if "-" in re.sub(r"<[^>]+>", "", str(r[isign])) else 1.0
        diff = num(r[idiff])
        store.upsert("TWSE", sym, r[iname].strip(), iso, {
            "close": close, "change": None if diff is None else sign * diff,
            "volume": num(r[ivol]), "high": num(r[ihigh]), "low": num(r[ilow])})
        n += 1
    log(f"TWSE {iso}: quotes {n}")

    t = get_json(TWSE["insti"].format(d8=d8))
    time.sleep(SLEEP)
    if t and t.get("stat") == "OK":
        f = t["fields"]
        ic = f.index("證券代號")
        ifo = f.index("外陸資買賣超股數(不含外資自營商)")
        ifd = f.index("外資自營商買賣超股數")
        itr = f.index("投信買賣超股數")
        ide = f.index("自營商買賣超股數")
        ito = f.index("三大法人買賣超股數")
        for r in t["data"]:
            sym = clean_code(r[ic])
            store.upsert("TWSE", sym, None, iso, {
                "foreign_net": (num(r[ifo]) or 0) + (num(r[ifd]) or 0),
                "trust_net": num(r[itr]), "dealer_net": num(r[ide]), "total_net": num(r[ito])})
        log(f"TWSE {iso}: insti {len(t['data'])}")

    m = get_json(TWSE["margin"].format(d8=d8))
    time.sleep(SLEEP)
    if m and m.get("stat") == "OK":
        mt = next((x for x in (m.get("tables") or []) if "融資融券彙總" in (x.get("title") or "")), None)
        if mt:
            # 欄位名重複(買進/賣出各兩組),用位置:0 代號 … 6 融資今日餘額 … 12 融券今日餘額
            for r in mt["data"]:
                store.upsert("TWSE", clean_code(r[0]), None, iso,
                             {"margin_bal": num(r[6]), "short_bal": num(r[12])})
            log(f"TWSE {iso}: margin {len(mt['data'])}")

    qf = get_json(TWSE["qfii"].format(d8=d8))
    time.sleep(SLEEP)
    if qf and qf.get("stat") == "OK":
        f = qf["fields"]
        ic = f.index("證券代號")
        ip = f.index("全體外資及陸資持股比率")
        for r in qf["data"]:
            store.upsert("TWSE", clean_code(r[ic]), None, iso, {"foreign_pct": num(r[ip])})
        log(f"TWSE {iso}: qfii {len(qf['data'])}")
    return True


# ── TPEx(只有當日)──────────────────────────────────────
def fetch_tpex_today(store):
    q = get_json(TPEX["quotes"])
    if not q or not isinstance(q, list):
        log("TPEx quotes: 無資料")
        return
    iso = roc_to_iso(q[0]["Date"])
    if store.has_day("TPEx", iso, "total_net"):
        log(f"TPEx {iso}: 已抓過,略過")
        return
    n = 0
    for r in q:
        close = num(r.get("Close"))
        if close is None or not is_stock_code(r["SecuritiesCompanyCode"]):
            continue
        store.upsert("TPEx", clean_code(r["SecuritiesCompanyCode"]), r["CompanyName"].strip(), iso, {
            "close": close, "change": num(r.get("Change")), "volume": num(r.get("TradingShares")),
            "high": num(r.get("High")), "low": num(r.get("Low"))})
        n += 1
    log(f"TPEx {iso}: quotes {n}")
    time.sleep(1)
    t = get_json(TPEX["insti"])
    if t and isinstance(t, list):
        for r in t:
            v = list(r.values())  # 欄名有怪空白,用位置:11 外資合計買賣超 14 投信 17 自營商 19 合計
            if len(v) < 20:
                continue
            store.upsert("TPEx", clean_code(v[1]), None, roc_to_iso(v[0]), {
                "foreign_net": num(v[11]), "trust_net": num(v[14]),
                "dealer_net": num(v[17]), "total_net": num(v[19])})
        log(f"TPEx {iso}: insti {len(t)}")
    time.sleep(1)
    m = get_json(TPEX["margin"])
    if m and isinstance(m, list):
        for r in m:
            store.upsert("TPEx", clean_code(r["SecuritiesCompanyCode"]), None, roc_to_iso(r["Date"]), {
                "margin_bal": num(r.get("MarginPurchaseBalance")), "short_bal": num(r.get("ShortSaleBalance"))})
        log(f"TPEx {iso}: margin {len(m)}")
    time.sleep(1)
    qf = get_json(TPEX["qfii"])
    if qf and isinstance(qf, list):
        for r in qf:
            store.upsert("TPEx", clean_code(r["SecuritiesCompanyCode"]), None, roc_to_iso(r["Date"]),
                         {"foreign_pct": num(r.get("PercentageOfSharesOC/FMIHeld"))})
        log(f"TPEx {iso}: qfii {len(qf)}")


def main():
    store = Store()
    tz8 = timezone(timedelta(hours=8))
    now = datetime.now(tz8)
    today = now.date()
    # 當天 15:30 前 TWSE 日報還沒出,從前一天開始
    start = today if now.hour >= 16 else today - timedelta(days=1)
    # 找最近 KEEP_DAYS 個交易日內還沒抓的日期(週末直接跳過),最多 MAX_BACKFILL 天
    done = 0
    d = start
    scanned = 0
    while scanned < 95 and done < MAX_BACKFILL:
        scanned += 1
        if d.weekday() < 5 and not store.has_day("TWSE", d.isoformat()):
            ok = fetch_twse_day(store, d)
            if ok:
                done += 1
        d -= timedelta(days=1)
        if (start - d).days > 90:
            break
    fetch_tpex_today(store)
    store.save(today.isoformat())


if __name__ == "__main__":
    sys.exit(main())
