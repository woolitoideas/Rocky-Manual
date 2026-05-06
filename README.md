# Rocky Manual

這個 repo 會把 Notion `Rocky 使用指南` 及其全部子頁，匯出成靜態網站。

## 功能
- 左側樹狀欄：子頁標題
- 可展開 / 收合多層子頁
- 每個 Notion page 都會輸出成靜態 HTML
- 圖片會下載到 `site/assets/media/`

## 本機路徑
`D:\Woolito Animation Dropbox\0_Woolito Animation Team Folder\Rocky\Manual`

## 同步方式

### 方式 1：手動同步
直接執行一鍵腳本：

```bash
./sync_site.sh
```

它會：
1. 重新抓 Notion 全部子頁
2. 重新產生靜態網站
3. 如果有變更就 commit + push
4. 如果沒有變更就結束

### 方式 2：每日自動同步
系統會每天檢查一次 Notion。
如果內容有更新，就自動更新靜態網站並 push 到 GitHub。

## 需求
- `NOTION_API_KEY` 必須可用
- 目標 Notion 頁面要已分享給 integration
