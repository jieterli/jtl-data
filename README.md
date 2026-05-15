# jtl-data

潔特力樂器社 APP 用的台股配息資料快取(從 FinMind 自動更新,GitHub Pages 提供 JSON)。

## 內容

- `dividends.json` — 23 檔精選股配息資料(每天台北 06:00 自動更新)
- `tools/finmind_fetch.py` — 抓取腳本(可獨立執行)
- `.github/workflows/update-dividends.yml` — GitHub Actions 排程

## 提供 URL

```
https://jieterli.github.io/jtl-data/dividends.json
```

## 維護方式

- 手動更新:Actions → Update dividends.json → Run workflow
- 改抓取清單:編輯 `tools/finmind_fetch.py` 的 `DEFAULT_PICKS` 或新增 `tools/picks.txt`(每行 `代號\t名稱`)

## Setup(首次)

1. Settings → Secrets and variables → Actions → New repository secret
   - Name: `FINMIND_TOKEN`
   - Value: (FinMind JWT,從 lib/secrets.dart 複製)
2. Settings → Pages → Source: Deploy from a branch → Branch: `main` / `(root)`
3. Actions → Update dividends.json → Run workflow(手動跑一次驗證)

© 2026 潔特力樂器社 Jieterli Music
