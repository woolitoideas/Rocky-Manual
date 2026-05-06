#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/mnt/d/Woolito Animation Dropbox/0_Woolito Animation Team Folder/Rocky/Manual"
cd "$ROOT_DIR"

export PATH="$HOME/.local/bin:$PATH"
export NOTION_ROOT_PAGE_ID="3589711f527180cdbe7fee7a34418b70"

if [[ -z "${NOTION_API_KEY:-}" && -f "$HOME/.hermes/.env" ]]; then
  export NOTION_API_KEY="$(grep '^NOTION_API_KEY=' "$HOME/.hermes/.env" | head -1 | cut -d= -f2- | tr -d '\r\n')"
fi

printf '[sync] regenerating static site...\n'
python sync_notion_site.py

if git diff --quiet -- site README.md sync_notion_site.py .gitignore sync_site.sh; then
  printf '[sync] no changes detected; nothing to commit.\n'
  exit 0
fi

STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
MSG="sync: update static site from Notion ($STAMP)"

printf '[sync] committing changes...\n'
git add site README.md sync_notion_site.py .gitignore sync_site.sh
git commit -m "$MSG"

printf '[sync] pushing to origin/main...\n'
git push

printf '[sync] done.\n'
