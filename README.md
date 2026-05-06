# Rocky Manual

這個 repo 會把 Notion `Rocky 使用指南` 及其全部子頁，匯出成靜態網站。

## 功能
- 左側樹狀欄：子頁標題
- 可展開 / 收合多層子頁
- 每個 Notion page 都會輸出成靜態 HTML
- 圖片會下載到 `site/assets/media/`

## 本機路徑
`D:\Woolito Animation Dropbox\0_Woolito Animation Team Folder\Rocky\Manual`

## 同步更新
當 Notion 有更新時，重新執行：

```bash
python sync_notion_site.py
```

接著把 `site/` 內的變更 commit + push 到 GitHub。

## 需求
- `NOTION_API_KEY` 必須可用
- 目標 Notion 頁面要已分享給 integration
