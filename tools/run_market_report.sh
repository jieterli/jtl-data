#!/bin/bash
# 台股盤後報告 —— 本機排程用的包裝腳本(由 ~/Library/LaunchAgents/com.jieterli.market-report.plist 呼叫)
#
# 為什麼需要它:GitHub Actions 的排程是「有空才跑」。2026-10-07 實測,
# 這支 workflow 的排程整天一次都沒被觸發,同 repo 的配息/籌碼排程也遲到 3~6 小時。
# → Mac 開著就由本機準時跑;GitHub Actions 留著當備援(晚跑只是寫同樣的數字,不衝突)。
#
# 做的事:git pull → 跑抓取 → 有變動才 commit + push。
# 休市日 / 資料還沒出 → 抓不到新東西 → 沒有變動 → 不 commit,安靜結束。

set -u
# repo 位置 = 這支腳本的上一層(從哪份 clone 跑就操作哪份)
# ⚠️ 排程用的那份一定要放在 ~/Desktop 之外:macOS TCC 不讓 launchd 背景程式
#    讀寫 Desktop/Documents/Downloads,會直接 "Operation not permitted"(exit 126),
#    而且不會跳授權視窗。2026-10-08 踩到。排程用 ~/jtl-data,桌面那份留著手動用。
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="/opt/homebrew/bin/python3"
GIT="/usr/bin/git"

cd "$REPO" || { echo "[$(date '+%F %T')] ❌ 找不到 repo:$REPO"; exit 1; }

echo "───────────────────────────────────────"
echo "[$(date '+%F %T')] 開始"

# 先同步,免得跟 GitHub Actions 自己 commit 的資料撞在一起
if ! "$GIT" pull --ff-only -q 2>&1; then
  echo "[$(date '+%F %T')] ⚠️ git pull 失敗(可能有本機未提交的改動),這次跳過"
  exit 1
fi

"$PYTHON" tools/market_report_fetch.py
rc=$?
if [ $rc -ne 0 ]; then
  echo "[$(date '+%F %T')] ❌ 抓取失敗(exit $rc)"
  exit $rc
fi

if [ -n "$("$GIT" status --porcelain report)" ]; then
  "$GIT" add report
  "$GIT" -c commit.gpgsign=false commit -q -m "auto(mac): market report $(date '+%F %H:%M')"
  if "$GIT" push -q 2>&1; then
    echo "[$(date '+%F %T')] ✅ 已更新並推上 GitHub"
  else
    echo "[$(date '+%F %T')] ⚠️ commit 成功但 push 失敗(下次會一起推)"
  fi
else
  echo "[$(date '+%F %T')] — 沒有變動(休市日,或資料還沒出)"
fi
