#!/usr/bin/env python3
"""
主題概念股自動產生腳本(themes.json)
-------------------------------------------------
台股「概念股 / 題材股」沒有免費 API,所以用 Claude AI 產生「當紅主題 + 成分股」,
再拿 TWSE(上市)+ TPEx(上櫃)官方股號清單**驗證、剔除 AI 可能寫錯的代號、用官方名稱回填**,
最後輸出 themes.json 供 GitHub Pages serve、APP 端自動抓。

⚠️ AI 有訓練資料時間點限制,「當紅」反映的是它已知的、廣為認知的台股題材,
   不是即時市場熱度;成分股經官方清單驗證,但仍非投資建議(海報上有免責)。

需要環境變數:
  ANTHROPIC_API_KEY  (GitHub Actions secret)
  OUTPUT_PATH        (預設 themes.json)
  ANTHROPIC_MODEL    (預設 claude-sonnet-4-6)
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"

TWSE_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
# 公司基本資料(含「產業別」)— 做主題歸類的硬驗證用
TWSE_INDUSTRY = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_INDUSTRY = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

# 「科技 / 特定產業」主題若出現這些產業別的股 = 八成放錯(航運股跑進連接器之類)→ 剔除。
# 注意:傳產 / 高股息 / 金融這類非科技主題不套用此黑名單(那裡本來就該有鋼鐵食品)。
_BASE_NONTECH_BLOCK = {
    "航運業", "食品工業", "水泥工業", "紡織纖維", "造紙工業", "玻璃陶瓷",
    "橡膠工業", "觀光餐旅", "觀光事業", "貿易百貨", "鋼鐵工業",
    "建材營造", "建材營造業", "運動休閒", "居家生活",
}
# 判斷主題是不是「科技 / 特定產業」(要套產業別硬驗證)的關鍵字
_TECH_THEME_KW = [
    "半導體", "晶圓", "封測", "記憶體", "IC", "ai", "AI", "伺服器", "雲端",
    "光", "矽", "CPO", "機器人", "自動化", "車用", "電動車", "電子", "網通",
    "網路", "PCB", "載板", "散熱", "連接器", "面板", "5G", "6G", "衛星",
    "重電", "綠能", "電力", "軍工", "航太", "無人機",
]


def _is_tech_theme(t: dict) -> bool:
    text = (str(t.get("title", "")) + str(t.get("subtitle", ""))).lower()
    return any(kw.lower() in text for kw in _TECH_THEME_KW)


def _block_set_for(t: dict) -> set:
    """依主題語意組黑名單:能源/重電題材放行油電燃氣;石化/生技/材料題材放行化學。"""
    text = str(t.get("title", "")) + str(t.get("subtitle", ""))
    block = set(_BASE_NONTECH_BLOCK)
    if not any(k in text for k in ("重電", "綠能", "電力", "能源", "石化", "油")):
        block.add("油電燃氣業")
    if not any(k in text for k in ("石化", "化學", "材料", "生技", "醫療", "能源")):
        block.update({"化學工業", "化學生技醫療"})
    return block

# 保護門檻:AI 產出 / 驗證後若主題數 < 此值,視為異常,不覆蓋線上好資料
MIN_THEMES = 4
MIN_STOCKS_PER_THEME = 2


def _http_json(url: str, timeout: int = 30, retries: int = 3):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 jtl-data themes_generate"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
    raise last_err


def _pick(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return str(v).strip()
    return ""


def fetch_official_codes() -> tuple[dict, bool]:
    """回傳 (code->官方名稱 dict, tpex_ok)。
    TWSE 一定要成功(否則 raise);TPEx 盡力而為(失敗則 tpex_ok=False)。
    """
    code_name: dict[str, str] = {}

    # 上市(TWSE)
    twse = _http_json(TWSE_ALL)
    for row in twse:
        code = _pick(row, "Code")
        name = _pick(row, "Name")
        if code and name:
            code_name[code] = name
    if len(code_name) < 500:
        raise RuntimeError(f"TWSE 只回 {len(code_name)} 檔(預期 >800)")
    print(f"   TWSE 上市 {len(code_name)} 檔", file=sys.stderr)

    # 上櫃(TPEx)— best effort
    tpex_ok = False
    try:
        tpex = _http_json(TPEX_ALL)
        before = len(code_name)
        for row in tpex:
            code = _pick(row, "SecuritiesCompanyCode", "Code", "code")
            name = _pick(row, "CompanyName", "Name", "name")
            if code and name and code not in code_name:
                code_name[code] = name
        added = len(code_name) - before
        if added > 100:
            tpex_ok = True
            print(f"   TPEx 上櫃 +{added} 檔", file=sys.stderr)
        else:
            print(f"   ⚠️ TPEx 只加到 {added} 檔,當作不可靠", file=sys.stderr)
    except Exception as e:
        print(f"   ⚠️ TPEx 抓取失敗({e}),只用上市清單驗證", file=sys.stderr)

    return code_name, tpex_ok


def fetch_industry_map() -> dict:
    """回傳 code -> 產業別 dict。TWSE 為主,TPEx best effort。失敗回空(= 不套硬驗證,安全降級)。"""
    ind: dict[str, str] = {}
    for url, label in ((TWSE_INDUSTRY, "TWSE"), (TPEX_INDUSTRY, "TPEx")):
        try:
            data = _http_json(url)
            for row in data:
                code = _pick(row, "公司代號", "SecuritiesCompanyCode", "Code", "code")
                industry = _pick(row, "產業別", "SecuritiesIndustryCode",
                                 "IndustryCategory", "industry")
                if code and industry:
                    ind.setdefault(code, industry)
            print(f"   產業別 {label}: 累計 {len(ind)} 檔", file=sys.stderr)
        except Exception as e:
            print(f"   ⚠️ 產業別 {label} 抓取失敗({e}),該來源略過", file=sys.stderr)
    return ind


def call_claude(api_key: str, model: str) -> str:
    prompt = (
        "你是嚴謹的台股產業分析助理。請列出當前台灣股市『廣為投資人認知的當紅主題 / 題材族群』,"
        "輸出成 JSON 給一個股息規劃 App 用來整理『主題概念股清單圖』。\n\n"
        "要求:\n"
        "1. 產生 6~8 個主題,每個主題底下用『子分類(角色)』分組,每組列 1~6 檔代表性個股。\n"
        "2. 只用『真實、目前在台股掛牌』的上市或上櫃股票,代號為阿拉伯數字(如 2330、6669、00929)。"
        "不要用已下市、興櫃、或你不確定的代號。\n"
        "3. 公司名稱用繁體中文(如 台積電、緯穎)。\n"
        "4. subtitle 用中性敘述產業是什麼,『絕對不要』出現看漲/該買/推薦/目標價等字眼"
        "(這是資訊整理,要守台灣投顧法規)。\n"
        "5. 涵蓋面要廣:除了半導體 / AI 伺服器,也可包含高股息 ETF、傳產、金融、生技、軍工、機器人、"
        "矽光子、CPO、重電、車用等當前熱門題材,挑你最有把握的。\n"
        "6. ★ 輝達(NVIDIA)供應鏈是當前台股最核心題材,請充分涵蓋 ★:"
        "AI 伺服器 / CPO 等主題裡,要納入輝達 GB200/GB300/Rubin 平台相關的台廠"
        "(伺服器 ODM、散熱 / 液冷、電源、CCL 載板、PCB、高速連接器 / 銅纜、HBM 相關等),"
        "用你最有把握的龍頭股。\n\n"
        "★ 分類準確度(最重要,請嚴格遵守)★\n"
        "A. 每一檔個股都必須『真的從事該主題、且真的屬於你放的那個子分類(角色)的業務』。"
        "例:面板廠(如群創、彩晶)不可放進『光纖載板』或『工廠自動化』;"
        "記憶體/晶圓代工廠(如力積電)不可放進『機器視覺』;"
        "線性滑軌廠(如上銀)不可放進『電源管理』。\n"
        "B. 寧缺勿濫:你不確定某檔的主業、或不確定它該歸哪個角色,就『直接不要列它』。"
        "少列正確的,遠勝多列放錯格子的。每組寧可只有 1~2 檔精準的。\n"
        "C. 嚴禁為了湊數把沾不上邊的個股硬塞進某個角色。\n"
        "D. 同一檔不要在『同一個主題內』重複出現;跨不同主題僅在它確實橫跨兩個產業時才可重複"
        "(如台達電可同時在電源與電動車)。\n"
        "E. 只列各角色裡你最有把握的龍頭 / 代表股,冷門股寧可略過。\n\n"
        "只輸出 JSON,不要任何說明文字,格式:\n"
        "{\n"
        '  "themes": [\n'
        "    {\n"
        '      "id": "english_slug",\n'
        '      "emoji": "🔬",\n'
        '      "title": "半導體",\n'
        '      "subtitle": "中性產業敘述",\n'
        '      "groups": [\n'
        '        {"role": "晶圓代工", "stocks": [{"symbol": "2330", "name": "台積電"}]}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}"
    )
    body = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    # Opus 產生較久 + runner 網路偶有瞬斷 → 拉長逾時 + 3 次重試(指數退避)
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                ANTHROPIC_URL,
                data=body,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            parts = payload.get("content", [])
            text = "".join(
                p.get("text", "") for p in parts if p.get("type") == "text")
            if not text.strip():
                raise RuntimeError(f"Claude 回傳空內容: {payload}")
            return text
        except Exception as e:
            last_err = e
            print(f"   Claude 第 {attempt+1}/3 次失敗({e}),等 {10*(attempt+1)} 秒重試...",
                  file=sys.stderr)
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Claude 連 3 次失敗: {last_err}")


def extract_json(text: str) -> dict:
    """從可能含 ```json 圍欄 / 物件後面還有多餘文字的回應中,穩健抽出第一個 JSON 物件。"""
    t = text.strip()
    # 去掉 ```json ... ``` 圍欄(若有)
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    start = t.find("{")
    if start == -1:
        raise ValueError("Claude 回應中找不到 JSON 物件")
    # 只解析「第一個完整物件」,忽略其後任何多餘文字(避免 'Extra data' 炸掉)
    obj, _ = json.JSONDecoder().raw_decode(t, start)
    return obj


def validate(raw: dict, code_name: dict, tpex_ok: bool,
             industry_map: dict | None = None) -> list[dict]:
    """驗證 AI 產出:
    1. 剔除官方清單查無 / 格式錯的代號,用官方名稱回填。
    2. 產業別硬驗證:科技 / 特定產業主題裡,產業明顯不搭的股(航運/石化…)剔除。
    """
    industry_map = industry_map or {}
    out = []
    dropped_code = 0   # 代號不存在 / 格式錯
    dropped_ind = 0    # 產業別不搭主題
    for t in raw.get("themes", []):
        if not isinstance(t, dict):
            continue
        block = _block_set_for(t) if _is_tech_theme(t) else set()
        groups = []
        for g in t.get("groups", []):
            if not isinstance(g, dict):
                continue
            stocks = []
            for s in g.get("stocks", []):
                if not isinstance(s, dict):
                    continue
                code = str(s.get("symbol", "")).strip()
                name = str(s.get("name", "")).strip()
                if not re.fullmatch(r"\d{4,6}", code):
                    dropped_code += 1
                    continue
                if code in code_name:
                    name = code_name[code]  # 用官方名稱(更準)
                elif tpex_ok:
                    # 官方清單完整卻查無 → AI 八成編錯,剔除
                    dropped_code += 1
                    continue
                if not name:
                    dropped_code += 1
                    continue
                # 產業別硬驗證:有查到產業、且落在該主題黑名單 → 剔除(航運股跑進科技主題等)
                industry = industry_map.get(code)
                if industry and any(b in industry for b in block):
                    dropped_ind += 1
                    continue
                stocks.append({"symbol": code, "name": name})
            role = str(g.get("role", "")).strip()
            if role and stocks:
                groups.append({"role": role, "stocks": stocks})
        title = str(t.get("title", "")).strip()
        tid = str(t.get("id", "")).strip()
        total = sum(len(g["stocks"]) for g in groups)
        if tid and title and groups and total >= MIN_STOCKS_PER_THEME:
            out.append({
                "id": tid,
                "emoji": str(t.get("emoji", "📊")).strip() or "📊",
                "title": title,
                "subtitle": str(t.get("subtitle", "")).strip(),
                "groups": groups,
            })
    print(f"   驗證後保留 {len(out)} 主題;剔除 代號可疑 {dropped_code} 檔、"
          f"產業不搭 {dropped_ind} 檔", file=sys.stderr)
    return out


def season_label(now: datetime) -> str:
    q = (now.month - 1) // 3 + 1
    return f"台股當紅主題 · {now.year} Q{q}"


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("❌ 環境變數 ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip()

    print("🔍 抓 TWSE/TPEx 官方股號清單...", file=sys.stderr)
    code_name, tpex_ok = fetch_official_codes()

    print("🏷  抓官方產業別(主題歸類硬驗證用)...", file=sys.stderr)
    industry_map = fetch_industry_map()

    print(f"🤖 呼叫 Claude ({model}) 產生主題...", file=sys.stderr)
    text = call_claude(api_key, model)
    raw = extract_json(text)
    themes = validate(raw, code_name, tpex_ok, industry_map)

    if len(themes) < MIN_THEMES:
        # 保護:產出太少 → 不覆蓋線上好資料,讓 Actions 紅燈通知
        raise RuntimeError(
            f"只產出 {len(themes)} 個有效主題(< {MIN_THEMES})— 暫停更新避免覆蓋好資料")

    now = datetime.now(timezone.utc)
    data = {
        "updated_at": now.isoformat(timespec="seconds"),
        "season_label": season_label(now),
        "source": f"Claude AI ({model}) 產生 + TWSE/TPEx 官方股號驗證",
        "theme_count": len(themes),
        "themes": themes,
    }
    out_path = os.environ.get("OUTPUT_PATH", "themes.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 寫入 {out_path}({len(themes)} 主題)", file=sys.stderr)


if __name__ == "__main__":
    main()
