# jtl-data — 雲端資料倉庫(給 Claude Code 看的工作筆記)

這個 repo 是 GitHub Pages 的資料來源,APP 端只讀公開 JSON,**APP 不直接打任何官方 API**。

| 輸出 | 腳本 | 排程(台北) | 誰在讀 |
| --- | --- | --- | --- |
| `dividends.json` | `tools/finmind_fetch.py` | 每天 06:00 | 股息婆婆(配息) |
| `themes.json` | `tools/themes_generate.py` | 每週一 06:30 | 股息婆婆(主題股) |
| `chips/` | `tools/chips_fetch.py` | 每天 18:40 | 股息婆婆(婆婆幫你整理) |
| `report/latest.json` | `tools/market_report_fetch.py` | 交易日 15:30 / 17:00 / 21:00 | 台股盤後報告(兩支 APP) |

---

## 🔴 資料源合法性 — 動手前先讀(2026-10-06 查證)

### 可以用

| 來源 | 授權 |
| --- | --- |
| `openapi.twse.com.tw` | 證交所開放 API,政府資料開放授權(<https://data.gov.tw/license>) |
| `www.tpex.org.tw/openapi` | 櫃買中心開放 API |
| `openapi.taifex.com.tw` | 期交所開放 API,政府資料開放授權 |
| `api.finmindtrade.com` | FinMind,需 `FINMIND_TOKEN`(GitHub secret 已設) |

引用時畫面要標「資料來源:臺灣證券交易所」等出處。

### 不可以用

`www.twse.com.tw/rwd/...`(證交所**網頁**端點)。使用條款
<https://www.twse.com.tw/zh/page/terms/use.html>「下載軟體或資料」一節明文:

> 非依臺灣證券交易所同意之方式……禁止透過包括但不限於自動化裝置、指令碼、自動程式、
> 蜘蛛程式、爬蟲程式或擷取程式等方式下載本網站之軟體或資料。

只有「已授權政府資料開放平臺提供公眾使用」的資料不在此限 —— 也就是開放 API 那些。
人工上網查沒問題,**排程自動抓不行**。

> ⚠️ **待辦(2026-10-06 記錄,胡老師決定先不動)**:既有的 `tools/chips_fetch.py`
> 有四個 TWSE 來源走 `www.twse.com.tw/rwd/...`(quotes / insti / qfii / margin),
> 每天 18:40 自動跑,資料餵給**已上架且有掛廣告**的股息婆婆。
> 其中「三大法人」已確認可用 FinMind 取代(數字實測完全一致),其他三項待評估。
> 新功能(`market_report_fetch.py`)完全沒有用到 rwd。

---

## report/ — 台股盤後報告

### 用到的端點與欄位(全部實際打過,2026-10-06)

| 區塊 | 端點 | 用到的欄位 |
| --- | --- | --- |
| 加權指數收盤 / 漲跌 | `/v1/exchangeReport/MI_INDEX` | `日期`(民國)、`指數`、`收盤指數`、`漲跌`(`+`/`-`)、`漲跌點數`、`漲跌百分比` |
| 加權指數開高低 | `/v1/indicesReport/MI_5MINS_HIST` | `Date`、`OpeningIndex`、`HighestIndex`、`LowestIndex`、`ClosingIndex` |
| 市場成交量值筆數 | `/v1/exchangeReport/FMTQIK` | `Date`、`TradeVolume`、`TradeValue`、`Transaction`、`TAIEX`、`Change` |
| 類股指數 | `/v1/exchangeReport/MI_INDEX` | 同上,取名稱結尾為「類指數」的列 |
| 個股(台積電) | `/v1/exchangeReport/STOCK_DAY_ALL` | `Code`、`Name`、`OpeningPrice`、`HighestPrice`、`LowestPrice`、`ClosingPrice`、`Change`、`TradeValue` |
| 三大法人買賣金額 | FinMind `TaiwanStockTotalInstitutionalInvestors` | `date`、`name`、`buy`、`sell`(`name` 有 `Dealer_self` / `Dealer_Hedging` / `Investment_Trust` / `Foreign_Investor` / `Foreign_Dealer_Self` / `total`) |
| 休市日 | `/v1/holidaySchedule/holidaySchedule` | `Name`、`Date`、`Weekday`、`Description` |

### ⏱ 時效:證交所開放 API 慢一天(2026-10-07 實測)

10/6 收盤九小時後(10/7 00:15)去打,`MI_INDEX` / `FMTQIK` / `STOCK_DAY_ALL`
最新仍然只到 **10/5**;FinMind 當天就有 10/6。所以:

| 區塊 | 主來源 | 備援 / 對帳 |
| --- | --- | --- |
| 大盤開高低收、成交量值筆數 | **FinMind** `TaiwanStockPrice` data_id=`TAIEX` | 證交所(同一天都有就比收盤,差 >1 點標 error) |
| 權值股(台積電) | **FinMind** `TaiwanStockPrice` | 證交所 `STOCK_DAY_ALL` |
| 三大法人 | **FinMind** `TaiwanStockTotalInstitutionalInvestors` | 無(證交所開放 API 沒有) |
| 類股指數 | 證交所 `MI_INDEX`(**只有這裡有**) | 無 → 會比大盤慢一天,照實標 `asOf` + `staleVsTradeDate` |

⚠️ **盤中防呆**:FinMind 盤中就給當天那根「還沒收盤」的 K。排程若在盤中跑,
拿半根當收盤價就是在發錯資訊 → `usable_finmind_rows()` 擋掉 14:30 前的當日資料。

⚠️ **日期混用是這個功能最容易犯的錯**。已經犯過兩次:
① APP 端拿 FinMind 歷史算均線時混到當天盤中那根(10/5 的報告配 10/6 的高點);
② 判讀句寫「資金流向 X」用的是慢一天的類股資料卻沒標日期。
每一筆數字都帶 `asOf`,畫面只要日期不同就要寫出來。

### 踩過的坑(別重複踩)

1. **證交所開放 API 沒有三大法人。** 143 個端點全列過,`BFI84U` 是「停資停券預告表」、
   `BFI61U` 是「公債補息」,都不是法人。法人只能走 FinMind(或 rwd,但那條禁止)。
2. **漲跌家數的開放版是壞的。** `/v1/opendata/twtazu_od` 2026-10-06 回的是 `1150605`
   (6/5)的資料,四個月沒更新 → 程式標 `unavailable`、畫面顯示「本日未提供」。
   **不准**拿舊數字當今天,也不准自己從個股數量去湊(統計範圍跟官方「股票」欄不同)。
   證交所哪天修好,程式會自動變 `ok`,不用改碼。
3. **開放 API 只給最近幾個交易日。** `FMTQIK` 與 `MI_5MINS_HIST` 實測只有 3 筆
   (10/1、10/2、10/5),查不到指定歷史日期。要補歷史只能靠自己每天存檔
   —— 這就是 `report/YYYY-MM-DD.json` 要留著的理由。
4. **漲跌是獨立欄位。** `漲跌點數` 本身**沒有**負號,正負看 `漲跌` 欄的 `+` / `-`。
   漏掉就會把下跌的日子顯示成上漲。
5. **類股有「大類」會重複計算。** `塑膠化工類指數` = 塑膠 + 化學 + 生技醫療,
   `水泥窯製類指數`、`機電類指數` 同理。排行榜要排除(`AGGREGATE_SECTORS`),
   但原始數字留在 `sectors.all` 供核對。
6. **休市日表混有交易日公告。** 名稱含「交易日」的(例「國曆新年開始交易日」)
   **不是**休市日,要略過 —— 跟辦公室 APP `market_calendar.dart` 同一套規則。
7. **颱風臨時休市不在那張表。** 辦公室 APP 的做法是「平日 09:00 後報價時間戳還停在
   前一日」自我偵測,不接天氣 API。

### 數字怎麼存

每個數字都是一個物件,帶自己的狀態、來源與資料日期;**APP 永遠不自己補值**。

```json
"close": { "value": 4971204, "scale": 2, "status": "ok", "source": "TWSE", "asOf": "2026-10-05" }
```

`value / 10**scale` 才是人看的數字(整數存,避開浮點誤差;億元換算只在畫面層做)。

| status | 意思 | 畫面 |
| --- | --- | --- |
| `ok` | 官方已公布且日期對得上 | 顯示數字 |
| `pending` | 今天還沒公布 | 「待公告」 |
| `unavailable` | 官方當日不提供 | 「本日未提供」 |
| `error` | 抓取或驗證失敗 | 「資料暫時無法取得」 |

規則:**已經是 `ok` 的欄位,後面的排程不准被空值覆蓋**(`merge_keep_ok`;一天跑三次
就是為了補不同時間才公布的欄位)。官方更正數字(兩邊都 `ok`)則蓋得掉。

### 兩支 APP 的分級(2026-10-06 胡老師定)

- `report/latest.json` = **只有事實**,股息婆婆讀。婆婆是上架 APP 且有掛廣告,
  投顧法紅線全開:價位、多空方向、操作建議一律不出現,連字串都不要存在。
- `report/latest_pro.json` = 事實 + 判讀,**股票分析辦公室**(`jtl_chips_personal`,
  個人自用不上架)讀。那支可以寫壓力支撐價位、方向、明日觀察、操作建議。
  **但數字一律程式算** —— 延續籌碼 APP 家規「AI 不准生價格」,語言模型只能拿算好的
  數字組句子,不准自己生數字。查得到就寫,查不到就留白。
  (`latest_pro.json` 還沒做,屬第二階段之後。)

### 怎麼跑

```bash
python tools/test_market_report.py          # 26 個測試,含線上實測
SKIP_LIVE=1 python tools/test_market_report.py   # 只跑離線測試(CI 用這個)
python tools/market_report_fetch.py --dry-run     # 印出來不寫檔
python tools/market_report_fetch.py              # 寫 report/
```

測試的 fixture 是真實 API 回傳(10/1、10/2、10/5 三個交易日),不是編的。
三大法人那組跟證交所 BFI82U 表的六行數字核對過,完全一致。

---

## ⏰ 排程:本機 launchd 為主,GitHub Actions 為輔(2026-10-08)

**GitHub Actions 的排程不可靠。** 2026-10-07 實測:`update-market-report.yml` 的排程
整天一次都沒被觸發(同 repo 的配息排程遲到 3 小時、籌碼遲到 6 小時)。
收盤後要馬上看到資料的功能不能只靠它。

| | 位置 | 時間 | 備註 |
| --- | --- | --- | --- |
| 主力 | Mac launchd `com.jieterli.market-report` | 交易日 15:35 / 17:05 / 21:05 | Mac 開著就準時 |
| 備援 | GitHub Actions | 同上(實際會遲到) | 晚跑只是寫同樣的數字,不衝突 |

- 排程用的 clone:**`~/jtl-data`**(不是桌面那份)。
  ⚠️ **macOS TCC 不讓 launchd 背景程式讀寫 `~/Desktop`** —— 放桌面會直接
  `Operation not permitted`(exit 126)而且不跳授權視窗。桌面那份留著手動開發用,
  兩份 push 到同一個 remote。
- 包裝腳本:`tools/run_market_report.sh`(git pull → 抓取 → 有變動才 commit + push)
- 紀錄:`~/Library/Logs/jtl-market-report.log`
- 停用:`launchctl bootout gui/$(id -u)/com.jieterli.market-report`
- 手動跑一次:`launchctl kickstart gui/$(id -u)/com.jieterli.market-report`
- 資料沒變時會沿用舊的 `generatedAt`,讓檔案內容不變 → 休市日不會累積空 commit。

## 部署 SOP

`git pull` → 改 → 跑測試 → `git push`。GitHub Actions 自動跑排程並 commit 資料檔。
金鑰一律用 GitHub secret(`FINMIND_TOKEN`、`ANTHROPIC_API_KEY`),不寫進程式碼。
