# 部署 jtl-data 到 GitHub(一次設定終身免費)

> 整個 jtl-data-repo 資料夾,你只要做這幾件事就讓它自動每天更新

## 1️⃣ 建 GitHub repo

- 到 https://github.com/new
- Owner:你的 GitHub 帳號(memory 寫的是 `jieterli`,如果是別的請告訴我改 Flutter 端 URL)
- Repository name:`jtl-data`
- Public(必選 — GitHub Pages 免費版只支援 public)
- **不要**勾 Add README(我們有自己的)
- Create repository

## 2️⃣ 把這個資料夾推上去

開 PowerShell 在這個資料夾下:

```powershell
cd D:\PhoneAPP\JTL_dividend_navigator\jtl-data-repo
git init
git add .
git commit -m "initial: FinMind dividend cache"
git branch -M main
git remote add origin https://github.com/jieterli/jtl-data.git
git push -u origin main
```

(如果是別的 GitHub 帳號,把 `jieterli` 換掉)

## 3️⃣ 加 FinMind Token 到 Secrets

- repo 頁 → Settings → Secrets and variables → Actions
- New repository secret
- Name:`FINMIND_TOKEN`
- Value:(從 `lib/secrets.dart` 複製 `finmindToken` 的值)
- Add secret

## 4️⃣ 開 GitHub Pages

- repo 頁 → Settings → Pages
- Source:Deploy from a branch
- Branch:`main` / `(root)`
- Save
- 等 30 秒 → 顯示 `Your site is live at https://jieterli.github.io/jtl-data/`

## 5️⃣ 測一次自動跑

- repo 頁 → Actions → Update dividends.json → Run workflow → Run workflow
- 等 1-2 分鐘 → 綠勾 → 點進去看 log
- 開啟 https://jieterli.github.io/jtl-data/dividends.json → 應該看到 JSON

## 6️⃣ APP 端測試

- 重新 build APP
- 開 scanner_page → 看殖利率是不是 FinMind 真值排序
- 加入私房推薦到日曆 → 看「資料來源:FinMind 公告值」字樣

## 之後的維護

- **自動**:每天台北 06:00 自動跑,無需動作
- **手動觸發**:repo Actions 頁 → Run workflow(任何時候)
- **改抓股清單**:編輯 `tools/finmind_fetch.py` 的 `DEFAULT_PICKS`,或建立 `tools/picks.txt`(每行 `代號<TAB>名稱`)

## 如果哪天 FinMind 改 schema 壞掉

- Actions 會 fail,GitHub 會寄通知到你的 GitHub email
- dividends.json 不會被覆蓋掉壞值,APP 繼續用上一份正常的
- 修 `tools/finmind_fetch.py` 後 push 即可
