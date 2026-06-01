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
import urllib.request
import urllib.error
from datetime import datetime, timezone

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"

TWSE_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

# 保護門檻:AI 產出 / 驗證後若主題數 < 此值,視為異常,不覆蓋線上好資料
MIN_THEMES = 4
MIN_STOCKS_PER_THEME = 2


def _http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 jtl-data themes_generate"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def call_claude(api_key: str, model: str) -> str:
    prompt = (
        "你是台股產業分析助理。請列出當前台灣股市『廣為投資人認知的當紅主題 / 題材族群』,"
        "輸出成 JSON 給一個股息規劃 App 用來整理『主題概念股清單圖』。\n\n"
        "要求:\n"
        "1. 產生 6~8 個主題,每個主題底下用『子分類(角色)』分組,每組列 1~6 檔代表性個股。\n"
        "2. 只用『真實、目前在台股掛牌』的上市或上櫃股票,代號為阿拉伯數字(如 2330、6669、00929)。"
        "不要用已下市、興櫃、或你不確定的代號。寧可少列也不要亂編。\n"
        "3. 公司名稱用繁體中文(如 台積電、緯穎)。\n"
        "4. subtitle 用中性敘述產業是什麼,『絕對不要』出現看漲/該買/推薦/目標價等字眼"
        "(這是資訊整理,要守台灣投顧法規)。\n"
        "5. 涵蓋面要廣:除了半導體 / AI 伺服器,也可包含高股息 ETF、傳產、金融、生技、軍工、機器人、"
        "矽光子、CPO、重電、車用等當前熱門題材,挑你最有把握的。\n\n"
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    parts = payload.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    if not text.strip():
        raise RuntimeError(f"Claude 回傳空內容: {payload}")
    return text


def extract_json(text: str) -> dict:
    """從可能含 ```json 圍欄的文字抽出 JSON。"""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.DOTALL)
    if m:
        t = m.group(1)
    else:
        # 退而求其次:抓第一個 { 到最後一個 }
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j != -1:
            t = t[i:j + 1]
    return json.loads(t)


def validate(raw: dict, code_name: dict, tpex_ok: bool) -> list[dict]:
    """驗證 AI 產出:剔除官方清單查無的代號,用官方名稱回填。"""
    out = []
    dropped = 0
    for t in raw.get("themes", []):
        if not isinstance(t, dict):
            continue
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
                    dropped += 1
                    continue
                if code in code_name:
                    name = code_name[code]  # 用官方名稱(更準)
                elif tpex_ok:
                    # 官方清單完整卻查無 → AI 八成編錯,剔除
                    dropped += 1
                    continue
                # tpex 不可靠時:查不到也保留(避免誤殺上櫃股),但要有名稱
                if not name:
                    dropped += 1
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
    print(f"   驗證後保留 {len(out)} 主題,剔除 {dropped} 檔可疑代號", file=sys.stderr)
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

    print(f"🤖 呼叫 Claude ({model}) 產生主題...", file=sys.stderr)
    text = call_claude(api_key, model)
    raw = extract_json(text)
    themes = validate(raw, code_name, tpex_ok)

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
