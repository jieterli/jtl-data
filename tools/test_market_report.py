#!/usr/bin/env python3
"""台股盤後報告的單元測試 — 鎖住交接文件「第一階段驗收清單」那幾條。

執行:python tools/test_market_report.py

⚠️ fixture 的數字是 2026-10-06 實際打證交所開放 API / FinMind 拿到的原始回傳,
   不是我編的。三大法人那組另外跟證交所 BFI82U 表對過,六行數字完全一致
   (兩個獨立來源互證)。胡老師若要再人工核對一次,對照頁面:
   https://www.twse.com.tw/zh/trading/historical/fmtqik.html(市場成交資訊)
   https://www.twse.com.tw/zh/fund/BFI82U.html(三大法人買賣金額)
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from market_report_fetch import (  # noqa: E402
    ERROR,
    OK,
    PENDING,
    UNAVAILABLE,
    build_advance_decline,
    build_heavyweights,
    build_holidays,
    build_institutional,
    build_report,
    build_sectors,
    build_taiex,
    merge_keep_ok,
    roc_to_iso,
    summarize,
    to_scaled,
    usable_finmind_rows,
    write_report,
)
from datetime import datetime, timedelta, timezone  # noqa: E402

TPE = timezone(timedelta(hours=8))

# FinMind 的 10/6(當天就有,證交所要隔天才出)
FM_TAIEX_ROWS = [
    {"date": "2026-10-05", "stock_id": "TAIEX", "Trading_Volume": 14490437804,
     "Trading_money": 1211041395113, "open": 48574.95, "max": 49770.66,
     "min": 48574.95, "close": 49712.04, "spread": 1236.30, "Trading_turnover": 5843674},
    {"date": "2026-10-06", "stock_id": "TAIEX", "Trading_Volume": 10889677530,
     "Trading_money": 1026204771139, "open": 49736.37, "max": 49968.92,
     "min": 49479.69, "close": 49822.55, "spread": 110.51, "Trading_turnover": 4923225},
]

# ── 真實回傳(2026-10-06 抓的)──────────────────────────────────

MI_INDEX_ROWS = [
    {"日期": "1151005", "指數": "寶島股價指數", "收盤指數": "55145.96",
     "漲跌": "+", "漲跌點數": "1,325.40", "漲跌百分比": "2.46"},
    {"日期": "1151005", "指數": "發行量加權股價指數", "收盤指數": "49712.04",
     "漲跌": "+", "漲跌點數": "1,236.30", "漲跌百分比": "2.55"},
    {"日期": "1151005", "指數": "塑膠類指數", "收盤指數": "450.03",
     "漲跌": "+", "漲跌點數": "28.04", "漲跌百分比": "6.64"},
    {"日期": "1151005", "指數": "電子零組件類指數", "收盤指數": "1047.55",
     "漲跌": "+", "漲跌點數": "50.80", "漲跌百分比": "5.10"},
    {"日期": "1151005", "指數": "塑膠化工類指數", "收盤指數": "1258.10",
     "漲跌": "+", "漲跌點數": "51.84", "漲跌百分比": "4.30"},
    {"日期": "1151005", "指數": "電子工業類指數", "收盤指數": "3181.22",
     "漲跌": "+", "漲跌點數": "92.78", "漲跌百分比": "3.00"},
    {"日期": "1151005", "指數": "半導體類指數", "收盤指數": "1686.25",
     "漲跌": "+", "漲跌點數": "46.51", "漲跌百分比": "2.84"},
    {"日期": "1151005", "指數": "航運類指數", "收盤指數": "207.59",
     "漲跌": "-", "漲跌點數": "3.47", "漲跌百分比": "-1.64"},
    {"日期": "1151005", "指數": "水泥類指數", "收盤指數": "123.35",
     "漲跌": "-", "漲跌點數": "1.53", "漲跌百分比": "-1.23"},
    {"日期": "1151005", "指數": "造紙類指數", "收盤指數": "267.09",
     "漲跌": "-", "漲跌點數": "2.54", "漲跌百分比": "-0.94"},
    {"日期": "1151005", "指數": "機電類指數", "收盤指數": "17485.64",
     "漲跌": "+", "漲跌點數": "504.66", "漲跌百分比": "2.97"},
]

TAIEX_HIST_ROWS = [
    {"Date": "1151001", "OpeningIndex": "47961.98", "HighestIndex": "48353.49",
     "LowestIndex": "47893.42", "ClosingIndex": "48353.49"},
    {"Date": "1151002", "OpeningIndex": "48390.65", "HighestIndex": "48491.62",
     "LowestIndex": "48205.81", "ClosingIndex": "48475.74"},
    {"Date": "1151005", "OpeningIndex": "48574.95", "HighestIndex": "49770.66",
     "LowestIndex": "48574.95", "ClosingIndex": "49712.04"},
]

FMTQIK_ROWS = [
    {"Date": "1151001", "TradeVolume": "10691867330", "TradeValue": "874046252416",
     "Transaction": "4715278", "TAIEX": "48353.49", "Change": "413.36"},
    {"Date": "1151002", "TradeVolume": "11017717304", "TradeValue": "938222196327",
     "Transaction": "4612433", "TAIEX": "48475.74", "Change": "122.25"},
    {"Date": "1151005", "TradeVolume": "14490437804", "TradeValue": "1211041395113",
     "Transaction": "5843674", "TAIEX": "49712.04", "Change": "1236.30"},
]

TSMC_ROW = {
    "Date": "1151005", "Code": "2330", "Name": "台積電", "TradeVolume": "26800187",
    "TradeValue": "68799148330", "OpeningPrice": "2550.00", "HighestPrice": "2580.00",
    "LowestPrice": "2545.00", "ClosingPrice": "2575.00", "Change": "75.0000",
    "Transaction": "105264",
}

INSTI_ROWS = [
    {"buy": 12475210779, "date": "2026-10-05", "name": "Dealer_self", "sell": 11059850275},
    {"buy": 0, "date": "2026-10-05", "name": "Foreign_Dealer_Self", "sell": 0},
    {"buy": 45597858723, "date": "2026-10-05", "name": "Dealer_Hedging", "sell": 36064721710},
    {"buy": 23300365779, "date": "2026-10-05", "name": "Investment_Trust", "sell": 28593525786},
    {"buy": 440691306171, "date": "2026-10-05", "name": "Foreign_Investor", "sell": 368794697926},
    {"buy": 522064741452, "date": "2026-10-05", "name": "total", "sell": 444512795697},
]

# 開放版 twtazu_od 2026-10-06 實際回傳 —— 停在 6/5,這就是它不能用的證據
STALE_BREADTH_ROWS = [
    {"出表日期": "1150605", "類型": "整體市場", "上漲": "3144", "漲停": "43",
     "下跌": "9578", "跌停": "355", "持平": "459"},
    {"出表日期": "1150605", "類型": "股票", "上漲": "342", "漲停": "19",
     "下跌": "671", "跌停": "10", "持平": "59"},
]

HOLIDAY_ROWS = [
    {"Name": "國曆新年開始交易日", "Date": "1150102", "Weekday": "五",
     "Description": "國曆新年開始交易。"},
    {"Name": "中華民國開國紀念日", "Date": "1160101", "Weekday": "五",
     "Description": "依規定放假1日。"},
    {"Name": "光復節", "Date": "1151024", "Weekday": "五", "Description": "依規定放假1日。"},
]

TRADE_DATE = "2026-10-05"

# 交接文件「法律紅線」那張表列的禁用詞
BANNED = ["偏多", "偏空", "多頭", "空頭", "續抱", "追價", "支撐", "壓力",
          "防守", "買進", "賣出", "加碼", "減碼", "看好", "看壞"]


def v(f):
    """把 value + scale 還原成人看得懂的數字。"""
    return None if f["value"] is None else f["value"] / (10 ** f["scale"])


class TestParsing(unittest.TestCase):
    def test_roc_date(self):
        self.assertEqual(roc_to_iso("1151005"), "2026-10-05")
        self.assertEqual(roc_to_iso("1150101"), "2026-01-01")
        self.assertIsNone(roc_to_iso("115100"))
        self.assertIsNone(roc_to_iso("1151350"), "13 月要擋掉")
        self.assertIsNone(roc_to_iso(""))

    def test_scaled(self):
        self.assertEqual(to_scaled("49712.04", 2), 4971204)
        self.assertEqual(to_scaled("1,236.30", 2), 123630)
        self.assertEqual(to_scaled("+1,236.30", 2), 123630)
        self.assertEqual(to_scaled("874046252416", 0), 874046252416)

    def test_missing_is_none_not_zero(self):
        """官方的缺值長相('--'、''、'X')要回 None。0 是『真的沒變動』,不一樣。"""
        for bad in ("--", "", "X", None, "  "):
            self.assertIsNone(to_scaled(bad, 2), f"{bad!r} 應該是 None")
        self.assertEqual(to_scaled("0.00", 2), 0, "真正的 0 要留著")


class TestThreeKnownTradingDays(unittest.TestCase):
    """驗收清單:拿 3 個已知交易日的數字核對。"""

    def test_20261005(self):
        t = build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS, "2026-10-05")
        self.assertEqual(v(t["close"]), 49712.04)
        self.assertEqual(v(t["change"]), 1236.30)
        self.assertEqual(v(t["changePct"]), 2.55)
        self.assertEqual(v(t["open"]), 48574.95)
        self.assertEqual(v(t["high"]), 49770.66)
        self.assertEqual(v(t["low"]), 48574.95)
        self.assertEqual(v(t["tradeValue"]), 1211041395113)
        self.assertEqual(v(t["transaction"]), 5843674)
        self.assertTrue(all(t[k]["status"] == OK for k in t))

    def test_20261002(self):
        t = build_taiex(
            [{"日期": "1151002", "指數": "發行量加權股價指數", "收盤指數": "48475.74",
              "漲跌": "+", "漲跌點數": "122.25", "漲跌百分比": "0.25"}],
            TAIEX_HIST_ROWS[:2], FMTQIK_ROWS[:2], "2026-10-02")
        self.assertEqual(v(t["close"]), 48475.74)
        self.assertEqual(v(t["change"]), 122.25)
        self.assertEqual(v(t["open"]), 48390.65)
        self.assertEqual(v(t["high"]), 48491.62)
        self.assertEqual(v(t["tradeValue"]), 938222196327)

    def test_20261001(self):
        t = build_taiex(
            [{"日期": "1151001", "指數": "發行量加權股價指數", "收盤指數": "48353.49",
              "漲跌": "+", "漲跌點數": "413.36", "漲跌百分比": "0.86"}],
            TAIEX_HIST_ROWS[:1], FMTQIK_ROWS[:1], "2026-10-01")
        self.assertEqual(v(t["close"]), 48353.49)
        self.assertEqual(v(t["change"]), 413.36)
        self.assertEqual(v(t["low"]), 47893.42)
        self.assertEqual(v(t["tradeValue"]), 874046252416)

    def test_down_day_keeps_minus_sign(self):
        """漲跌是獨立欄位('+' / '-'),點數本身沒有負號 —— 漏掉就變成跌的日子顯示上漲。"""
        t = build_taiex(
            [{"日期": "1151005", "指數": "發行量加權股價指數", "收盤指數": "48000.00",
              "漲跌": "-", "漲跌點數": "500.00", "漲跌百分比": "-1.03"}],
            [], [], "2026-10-05")
        self.assertEqual(v(t["change"]), -500.0)
        self.assertEqual(v(t["changePct"]), -1.03)


class TestSectors(unittest.TestCase):
    def test_top_gainers_excludes_aggregate_indices(self):
        """塑膠化工類 / 機電類是『大類』,跟子類一起排會重複計算。"""
        s = build_sectors(MI_INDEX_ROWS, TRADE_DATE)
        self.assertEqual(s["topGainers"][:2], ["塑膠類指數", "電子零組件類指數"])
        self.assertNotIn("塑膠化工類指數", s["topGainers"])
        self.assertNotIn("機電類指數", s["topGainers"])
        self.assertIn("塑膠化工類指數", [i["name"] for i in s["all"]],
                      "大類不排名,但原始數字要留著可核對")

    def test_top_losers_worst_first(self):
        s = build_sectors(MI_INDEX_ROWS, TRADE_DATE)
        self.assertEqual(s["topLosers"], ["航運類指數", "水泥類指數", "造紙類指數"])

    def test_main_index_is_not_a_sector(self):
        s = build_sectors(MI_INDEX_ROWS, TRADE_DATE)
        names = [i["name"] for i in s["all"]]
        self.assertNotIn("發行量加權股價指數", names)
        self.assertNotIn("寶島股價指數", names)


class TestHeavyweightAndInstitutional(unittest.TestCase):
    def test_tsmc(self):
        h = build_heavyweights([TSMC_ROW], TRADE_DATE)["2330"]
        self.assertEqual(h["name"], "台積電")
        self.assertEqual(v(h["close"]), 2575.0)
        self.assertEqual(v(h["change"]), 75.0)
        self.assertEqual(v(h["high"]), 2580.0)

    def test_institutional_matches_official_table(self):
        i = build_institutional(INSTI_ROWS, TRADE_DATE)
        self.assertEqual(v(i["foreign"]["buy"]), 440691306171)
        self.assertEqual(v(i["investmentTrust"]["net"]), 23300365779 - 28593525786)
        self.assertEqual(v(i["total"]["net"]), 77551945755)
        self.assertEqual(i["foreign"]["label"], "外資及陸資(不含外資自營商)")

    def test_foreign_dealer_listed_separately(self):
        """外資自營商照官方分法單獨一行,不自行併進外資。"""
        i = build_institutional(INSTI_ROWS, TRADE_DATE)
        self.assertEqual(v(i["foreignDealer"]["buy"]), 0)
        self.assertEqual(i["foreignDealer"]["buy"]["status"], OK, "0 是官方真值,不是缺值")
        self.assertEqual(v(i["foreign"]["buy"]), 440691306171)


class TestMissingData(unittest.TestCase):
    """驗收清單:『尚未公布』要是 pending,不能當成 ok。"""

    def test_old_date_is_pending_not_ok(self):
        t = build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS, "2026-10-06")
        self.assertEqual(t["close"]["status"], PENDING)
        self.assertIsNone(t["close"]["value"], "日期不符就不准給值")
        self.assertEqual(t["close"]["asOf"], "2026-10-05", "照實記資料源的日期")

    def test_institutional_not_published_yet(self):
        i = build_institutional([], TRADE_DATE)
        self.assertEqual(i["total"]["buy"]["status"], PENDING)
        self.assertIsNone(i["total"]["net"]["value"])

    def test_stale_open_data_breadth_is_unavailable(self):
        """開放版漲跌家數停在 6/5 → 標 unavailable,絕不拿舊數字當今天。"""
        b = build_advance_decline(STALE_BREADTH_ROWS, TRADE_DATE)
        self.assertEqual(b["advances"]["status"], UNAVAILABLE)
        self.assertIsNone(b["advances"]["value"])
        self.assertEqual(b["advances"]["asOf"], "2026-06-05")

    def test_breadth_works_if_twse_ever_fixes_it(self):
        """哪天證交所把開放版修好,解析要正確(含去掉『(漲停)』那段)。"""
        rows = [{"出表日期": "1151005", "類型": "股票", "上漲": "364(27)",
                 "下跌": "631(0)", "持平": "87"}]
        b = build_advance_decline(rows, TRADE_DATE)
        self.assertEqual(v(b["advances"]), 364)
        self.assertEqual(v(b["declines"]), 631)
        self.assertEqual(v(b["unchanged"]), 87)
        self.assertEqual(b["advances"]["status"], OK)


class TestHolidays(unittest.TestCase):
    def test_skips_trading_day_announcements(self):
        """『開始交易日』『最後交易日』是交易日公告,不是休市 —— 辦公室 APP 踩過。"""
        h = build_holidays(HOLIDAY_ROWS, "2026-10-06")
        names = [i["name"] for i in h["items"]]
        self.assertNotIn("國曆新年開始交易日", names)
        self.assertEqual(names, ["光復節", "中華民國開國紀念日"])

    def test_only_future(self):
        h = build_holidays(HOLIDAY_ROWS, "2026-11-01")
        self.assertEqual([i["date"] for i in h["items"]], ["2027-01-01"])


class TestNoOverwrite(unittest.TestCase):
    """驗收清單:先寫 ok 再跑一次失敗的抓取,ok 值必須保留。"""

    def test_ok_survives_later_pending(self):
        old = {"taiex": {"close": {"value": 4971204, "scale": 2, "status": OK,
                                   "source": "TWSE", "asOf": TRADE_DATE}}}
        new = {"taiex": {"close": {"value": None, "scale": 2, "status": PENDING,
                                   "source": "TWSE", "asOf": None}}}
        merged = merge_keep_ok(old, new)
        self.assertEqual(merged["taiex"]["close"]["value"], 4971204)
        self.assertEqual(merged["taiex"]["close"]["status"], OK)

    def test_official_correction_still_applies(self):
        """官方更正數字(兩邊都 ok)要蓋得掉,不然就修不回來了。"""
        old = {"close": {"value": 1, "scale": 2, "status": OK, "source": "TWSE", "asOf": TRADE_DATE}}
        new = {"close": {"value": 2, "scale": 2, "status": OK, "source": "TWSE", "asOf": TRADE_DATE}}
        self.assertEqual(merge_keep_ok(old, new)["close"]["value"], 2)

    def test_pending_fills_in_later(self):
        old = {"x": {"value": None, "scale": 0, "status": PENDING, "source": "TAIFEX", "asOf": None}}
        new = {"x": {"value": 5, "scale": 0, "status": OK, "source": "TAIFEX", "asOf": TRADE_DATE}}
        self.assertEqual(merge_keep_ok(old, new)["x"]["value"], 5)

    def test_second_run_does_not_wipe_file(self):
        """整支跑第二次(抓取全失敗)不可以把檔案洗成空的。"""
        report = {
            "tradeDate": TRADE_DATE,
            "taiex": {"close": {"value": 4971204, "scale": 2, "status": OK,
                                "source": "TWSE", "asOf": TRADE_DATE}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            write_report(report, tmp)
            failed = {
                "tradeDate": TRADE_DATE,
                "taiex": {"close": {"value": None, "scale": 2, "status": "error",
                                    "source": "TWSE", "asOf": None}},
            }
            write_report(failed, tmp)
            with open(os.path.join(tmp, "report", "latest.json"), encoding="utf-8") as fh:
                saved = json.load(fh)
        self.assertEqual(saved["taiex"]["close"]["value"], 4971204)


class TestNoAdviceWords(unittest.TestCase):
    """驗收清單:禁用詞檢查。這份事實層 JSON 兩支 APP 共用,一個字都不能有。"""

    def _strings(self, obj):
        if isinstance(obj, dict):
            for k, val in obj.items():
                yield str(k)
                yield from self._strings(val)
        elif isinstance(obj, list):
            for item in obj:
                yield from self._strings(item)
        elif isinstance(obj, str):
            yield obj

    def test_report_has_no_banned_words(self):
        report = {
            "taiex": build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS, TRADE_DATE),
            "breadth": build_advance_decline(STALE_BREADTH_ROWS, TRADE_DATE),
            "heavyweights": build_heavyweights([TSMC_ROW], TRADE_DATE),
            "sectors": build_sectors(MI_INDEX_ROWS, TRADE_DATE),
            "institutional": build_institutional(INSTI_ROWS, TRADE_DATE),
            "holidays": build_holidays(HOLIDAY_ROWS, "2026-10-06"),
            "attribution": "資料來源:臺灣證券交易所(開放資料)、FinMind",
            "disclaimer": "本頁僅彙整臺灣證券交易所公開資料,不提供任何投資建議。",
        }
        blob = "".join(self._strings(report))
        for word in BANNED:
            self.assertNotIn(word, blob, f"事實層 JSON 出現禁用詞「{word}」")

    def test_summary_lines_have_no_banned_words(self):
        report = {
            "tradeDate": TRADE_DATE,
            "isLatestTradingDayToday": False,
            "taiex": build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS, TRADE_DATE),
            "breadth": build_advance_decline(STALE_BREADTH_ROWS, TRADE_DATE),
            "heavyweights": build_heavyweights([TSMC_ROW], TRADE_DATE),
            "sectors": build_sectors(MI_INDEX_ROWS, TRADE_DATE),
            "institutional": build_institutional(INSTI_ROWS, TRADE_DATE),
            "holidays": build_holidays(HOLIDAY_ROWS, "2026-10-06"),
        }
        text = summarize(report)
        for word in BANNED:
            self.assertNotIn(word, text, f"摘要出現禁用詞「{word}」")


class TestLiveApi(unittest.TestCase):
    """真的連線打一次(需要網路)。設 SKIP_LIVE=1 可跳過。"""

    @unittest.skipIf(os.environ.get("SKIP_LIVE") == "1", "SKIP_LIVE=1")
    def test_live_fetch_shape(self):
        report = build_report()
        self.assertRegex(report["tradeDate"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn(report["taiex"]["close"]["status"], (OK, PENDING))
        if report["taiex"]["close"]["status"] == OK:
            close = v(report["taiex"]["close"])
            self.assertGreater(close, 10000, "加權指數不可能低於一萬點,解析一定錯了")
            self.assertLess(close, 200000)
        print("\n── 線上實測 ──\n" + summarize(report))


class TestFinMindFirst(unittest.TestCase):
    """2026-10-07 實測:證交所開放 API 收盤九小時後仍只有前一天,FinMind 當天就有。
    所以大盤改以 FinMind 為主、證交所對帳。"""

    def test_uses_finmind_for_today(self):
        t = build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS,
                        "2026-10-06", fm_rows=FM_TAIEX_ROWS)
        self.assertEqual(v(t["close"]), 49822.55)
        self.assertEqual(v(t["change"]), 110.51)
        self.assertEqual(v(t["high"]), 49968.92)
        self.assertEqual(v(t["tradeValue"]), 1026204771139)
        self.assertEqual(t["close"]["source"], "FinMind")
        self.assertEqual(t["close"]["status"], OK)

    def test_change_pct_computed_from_prev_close(self):
        t = build_taiex([], [], [], "2026-10-06", fm_rows=FM_TAIEX_ROWS)
        # 110.51 / 49712.04 = 0.2223%
        self.assertAlmostEqual(v(t["changePct"]), 0.22, places=2)

    def test_falls_back_to_twse_when_finmind_missing_that_day(self):
        t = build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS,
                        "2026-10-05", fm_rows=[])
        self.assertEqual(v(t["close"]), 49712.04)
        self.assertEqual(t["close"]["source"], "TWSE")

    def test_two_sources_disagree_is_error_not_a_guess(self):
        """兩邊同一天卻對不起來 → 標 error,寧可不顯示也不顯示錯的。"""
        bad = [dict(FM_TAIEX_ROWS[0], close=48000.0)]
        t = build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS,
                        "2026-10-05", fm_rows=bad)
        self.assertEqual(t["close"]["status"], ERROR)
        self.assertIsNone(t["close"]["value"])
        self.assertIn("對不起來", t["close"]["note"])

    def test_two_sources_agree_passes(self):
        t = build_taiex(MI_INDEX_ROWS, TAIEX_HIST_ROWS, FMTQIK_ROWS,
                        "2026-10-05", fm_rows=FM_TAIEX_ROWS)
        self.assertEqual(t["close"]["status"], OK)
        self.assertEqual(v(t["close"]), 49712.04)


class TestIntradayGuard(unittest.TestCase):
    """台股 13:30 收盤。盤中跑排程時 FinMind 給的是還沒收盤的半根,
    拿去當收盤價就是發錯資訊 —— 一律擋掉。"""

    def test_drops_todays_bar_before_close(self):
        now = datetime(2026, 10, 6, 11, 0, tzinfo=TPE)  # 盤中
        rows = usable_finmind_rows(FM_TAIEX_ROWS, now)
        self.assertEqual([r["date"] for r in rows], ["2026-10-05"])

    def test_keeps_todays_bar_after_settle(self):
        now = datetime(2026, 10, 6, 15, 30, tzinfo=TPE)  # 收盤後
        rows = usable_finmind_rows(FM_TAIEX_ROWS, now)
        self.assertEqual([r["date"] for r in rows], ["2026-10-05", "2026-10-06"])

    def test_boundary_1430(self):
        self.assertEqual(
            len(usable_finmind_rows(FM_TAIEX_ROWS, datetime(2026, 10, 6, 14, 29, tzinfo=TPE))), 1)
        self.assertEqual(
            len(usable_finmind_rows(FM_TAIEX_ROWS, datetime(2026, 10, 6, 14, 30, tzinfo=TPE))), 2)

    def test_never_future(self):
        now = datetime(2026, 10, 5, 16, 0, tzinfo=TPE)
        rows = usable_finmind_rows(FM_TAIEX_ROWS, now)
        self.assertEqual([r["date"] for r in rows], ["2026-10-05"])


class TestSectorsKeepOwnDate(unittest.TestCase):
    """類股只有證交所有,會比大盤慢一天。整區消失不行,假裝是當天的也不行 ——
    照實記自己的 asOf,並標 staleVsTradeDate 讓畫面寫出來。"""

    def test_keeps_data_with_own_date(self):
        s = build_sectors(MI_INDEX_ROWS, "2026-10-06")
        self.assertTrue(s["topGainers"], "類股不該因為慢一天就空掉")
        self.assertEqual(s["asOf"], "2026-10-05")
        self.assertTrue(s["staleVsTradeDate"])

    def test_not_stale_when_same_day(self):
        s = build_sectors(MI_INDEX_ROWS, "2026-10-05")
        self.assertFalse(s["staleVsTradeDate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
