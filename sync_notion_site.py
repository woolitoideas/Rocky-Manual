#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
import re
import shutil
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

ROOT_PAGE_ID = os.environ.get('NOTION_ROOT_PAGE_ID', '3589711f527180cdbe7fee7a34418b70')
OUTPUT_DIR = Path(os.environ.get('OUTPUT_DIR', 'site'))
ASSET_DIR = OUTPUT_DIR / 'assets'
MEDIA_DIR = ASSET_DIR / 'media'
BRAND_ICON_SOURCE = Path(os.environ.get('BRAND_ICON_SOURCE', 'rocky-home-icon.jpeg'))
SKILLS_ROOT = Path(os.environ.get('HERMES_SKILLS_ROOT', '/home/user/.hermes/skills'))
SKILL_LIST_PAGE_ID = '3589711f527180c58d18f71918c38ec9'
skill_catalog_cache: list[dict] | None = None
API_KEY = os.environ.get('NOTION_API_KEY')
NOTION_VERSION = os.environ.get('NOTION_VERSION', '2025-09-03')
BASE_URL = os.environ.get('NOTION_BASE_URL', 'https://api.notion.com/v1')

if not API_KEY:
    print('ERROR: NOTION_API_KEY is not set', file=sys.stderr)
    sys.exit(1)

HEADERS = {
    'Authorization': f'Bearer {API_KEY}',
    'Notion-Version': NOTION_VERSION,
    'Content-Type': 'application/json',
}

page_cache: dict[str, dict] = {}
children_cache: dict[str, list] = {}


def normalize_id(s: str) -> str:
    return s.replace('-', '')


def short_id(s: str) -> str:
    return normalize_id(s)[-12:]


def slugify(title: str) -> str:
    s = unicodedata.normalize('NFKD', title)
    s = s.encode('ascii', 'ignore').decode('ascii').lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s or 'page'


def page_filename(page_id: str, title: str) -> str:
    if normalize_id(page_id) == normalize_id(ROOT_PAGE_ID):
        return 'index.html'
    return f'{slugify(title)}-{short_id(page_id)}.html'


def api_request(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(BASE_URL + path, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'Notion API {method} {path} failed: {e.code} {e.reason}\n{detail}') from e


def get_page(page_id: str) -> dict:
    page_id = normalize_id(page_id)
    if page_id not in page_cache:
        page_cache[page_id] = api_request('GET', f'/pages/{page_id}')
    return page_cache[page_id]


def rich_text_plain(items: list[dict]) -> str:
    return ''.join(item.get('plain_text', '') for item in items or [])


def page_title(page_id: str) -> str:
    page = get_page(page_id)
    for prop in page.get('properties', {}).values():
        if prop.get('type') == 'title':
            return rich_text_plain(prop.get('title', []))
    return page_id


def get_children(block_id: str) -> list[dict]:
    block_id = normalize_id(block_id)
    if block_id in children_cache:
        return children_cache[block_id]
    results = []
    cursor = None
    while True:
        suffix = '?page_size=100'
        if cursor:
            suffix += '&start_cursor=' + urllib.parse.quote(cursor)
        data = api_request('GET', f'/blocks/{block_id}/children{suffix}')
        results.extend(data.get('results', []))
        if not data.get('has_more'):
            break
        cursor = data.get('next_cursor')
    children_cache[block_id] = results
    return results


def build_block_tree(block_id: str) -> list[dict]:
    tree = []
    for block in get_children(block_id):
        node = dict(block)
        if block.get('type') == 'child_page':
            node['_children'] = []
        else:
            node['_children'] = build_block_tree(block['id']) if block.get('has_children') else []
        tree.append(node)
    return tree


def scan_child_pages(blocks: list[dict]) -> list[tuple[str, str]]:
    found = []
    for block in blocks:
        if block.get('type') == 'child_page':
            found.append((normalize_id(block['id']), block.get('child_page', {}).get('title', 'Untitled page')))
        found.extend(scan_child_pages(block.get('_children', [])))
    return found


def discover_pages(root_id: str) -> dict[str, dict]:
    pages: dict[str, dict] = {}
    visiting: set[str] = set()

    def walk(page_id: str):
        page_id = normalize_id(page_id)
        if page_id in pages or page_id in visiting:
            return
        visiting.add(page_id)
        title = page_title(page_id)
        blocks = build_block_tree(page_id)
        node = {
            'id': page_id,
            'title': title,
            'filename': page_filename(page_id, title),
            'blocks': blocks,
            'child_pages': [],
        }
        pages[page_id] = node
        node['child_pages'] = scan_child_pages(blocks)
        for cid, _ in node['child_pages']:
            walk(cid)
        visiting.remove(page_id)

    walk(root_id)
    return pages


def rich_text_html(items: list[dict]) -> str:
    out = []
    for item in items or []:
        text = html.escape(item.get('plain_text', ''))
        ann = item.get('annotations', {}) or {}
        if ann.get('code'):
            text = f'<code>{text}</code>'
        if ann.get('bold'):
            text = f'<strong>{text}</strong>'
        if ann.get('italic'):
            text = f'<em>{text}</em>'
        if ann.get('strikethrough'):
            text = f'<s>{text}</s>'
        if ann.get('underline'):
            text = f'<u>{text}</u>'
        href = item.get('href')
        if href:
            text = f'<a href="{html.escape(href)}" target="_blank" rel="noreferrer noopener">{text}</a>'
        out.append(text)
    return ''.join(out)


def download_asset(url: str) -> Path:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix or (mimetypes.guess_extension('image/png') or '.bin')
    name = re.sub(r'[^a-z0-9]+', '-', Path(parsed.path).stem.lower()).strip('-') or 'asset'
    digest = base64.urlsafe_b64encode(url.encode('utf-8')).decode('ascii').rstrip('=')[:12]
    out = MEDIA_DIR / f'{name}-{digest}{ext}'
    if out.exists():
        return out.relative_to(OUTPUT_DIR)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        out.write_bytes(resp.read())
    return out.relative_to(OUTPUT_DIR)


def parse_skill_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding='utf-8')
    if not text.startswith('---'):
        return {}
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def skill_tags_from_frontmatter(data: dict) -> list[str]:
    tags = []
    metadata = data.get('metadata')
    if isinstance(metadata, dict):
        hermes = metadata.get('hermes')
        if isinstance(hermes, dict):
            tags = hermes.get('tags') or []
    if not tags:
        tags = data.get('tags') or []
    if isinstance(tags, str):
        tags = [tags]
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        tag = str(tag).strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(tag)
    return cleaned


def load_skill_catalog() -> list[dict]:
    global skill_catalog_cache
    if skill_catalog_cache is not None:
        return skill_catalog_cache
    skills: list[dict] = []
    if SKILLS_ROOT.exists():
        for path in sorted(SKILLS_ROOT.rglob('SKILL.md')):
            data = parse_skill_frontmatter(path)
            name = str(data.get('name') or path.parent.name).strip() or path.parent.name
            description = str(data.get('description') or '').strip()
            tags = skill_tags_from_frontmatter(data)
            rel = path.relative_to(SKILLS_ROOT)
            category = rel.parts[0] if len(rel.parts) > 1 else path.parent.name
            skill_dir = '/'.join(rel.parts[:-1])
            search_blob = ' '.join([name, description, category, skill_dir, *tags]).lower()
            skills.append({
                'name': name,
                'description': description,
                'tags': tags,
                'category': category,
                'skill_dir': skill_dir,
                'search_blob': search_blob,
            })
    skills.sort(key=lambda item: (item['category'].lower(), item['name'].lower()))
    skill_catalog_cache = skills
    return skills


def render_skill_catalog_page(page_id: str, pages: dict[str, dict]) -> str:
    page = pages[normalize_id(page_id)]
    skills = load_skill_catalog()
    skill_map: dict[str, dict] = {}
    for skill in skills:
        skill_map[skill['name'].lower()] = skill

    body = render_children(page['blocks'], pages)

    def inject_skill_attrs(html_body: str) -> str:
        for skill in skill_map.values():
            search_blob = html.escape(skill['search_blob'], quote=True)
            pattern = f'<li><strong>{html.escape(skill["name"])}</strong>'
            replacement = (
                f'<li class="skill-entry" data-search="{search_blob}">'
                f'<strong>{html.escape(skill["name"])}</strong>'
            )
            html_body = html_body.replace(pattern, replacement, 1)
        return html_body

    body = inject_skill_attrs(body)
    body = body.replace('<ul class="notion-list">', '<ul class="notion-list skill-list">')

    toolbar_template = r'''
      <section class="skill-browser">
        <p class="skill-intro">已整理成兩類：內建 skill、我們訓練的 skill。這頁現在可以直接搜尋 skill。</p>
        <div class="skill-toolbar">
          <label class="skill-search">
            <span>搜尋 skill</span>
            <input id="skill-search" type="search" placeholder="搜尋名稱、描述或分類">
          </label>
          <button class="skill-reset" type="button" id="skill-reset">清除搜尋</button>
        </div>
        <div class="skill-stats">
          <span id="skill-visible-count">__TOTAL__</span>
          <span>/ __TOTAL__ 個 skill</span>
        </div>
        <p class="skill-empty" id="skill-empty" hidden>沒有符合條件的 skill，試試清空搜尋。</p>
      </section>
      <script>
      (() => {
        const search = document.getElementById('skill-search');
        const reset = document.getElementById('skill-reset');
        const entries = Array.from(document.querySelectorAll('.skill-entry'));
        const visibleCount = document.getElementById('skill-visible-count');
        const empty = document.getElementById('skill-empty');

        function applyFilters() {
          const q = search.value.trim().toLowerCase();
          let visible = 0;
          entries.forEach((entry) => {
            const matchesSearch = !q || entry.dataset.search.includes(q);
            entry.hidden = !matchesSearch;
            if (matchesSearch) visible += 1;
          });
          visibleCount.textContent = visible;
          empty.hidden = visible !== 0;
        }

        search.addEventListener('input', applyFilters);
        reset.addEventListener('click', () => {
          search.value = '';
          applyFilters();
          search.focus();
        });
        applyFilters();
      })();
      </script>
    '''
    toolbar = toolbar_template.replace('__TOTAL__', str(len(skills)))

    sidebar = build_sidebar(pages, page_id)
    title = html.escape(page['title'])
    breadcrumb_html = f'<a href="index.html">{html.escape(pages[normalize_id(ROOT_PAGE_ID)]["title"])}</a><span class="sep">/</span><a href="skill-f71918c38ec9.html">Skill 列表</a>'
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-head">
        <div class="brand">
          <a class="brand-link" href="index.html" aria-label="回到首頁">
            <img class="brand-icon" src="assets/media/rocky-home-icon.jpeg" alt="Rocky 使用指南" />
          </a>
          <div>
            <div class="brand-title">Rocky 使用指南</div>
          </div>
        </div>
        <button class="sidebar-toggle" type="button" aria-expanded="true" aria-label="收合側欄" title="收合側欄">☰</button>
      </div>
      <a class="sidebar-home" href="index.html">指南首頁</a>
      <nav class="nav-tree">{sidebar}</nav>
    </aside>
    <main class="content">
      <div class="topbar">
        <div class="breadcrumbs">{breadcrumb_html}</div>
        <div class="page-id">{short_id(page_id)}</div>
      </div>
      <article class="page-card">
        <header class="page-header">
          <h1>{title}</h1>
        </header>
        <section class="page-body">{toolbar}{body}</section>
      </article>
    </main>
  </div>
  <script src="assets/pages.js"></script>
  <script src="assets/router.js"></script>
</body>
</html>"""


def render_children(blocks: list[dict], pages: dict[str, dict]) -> str:
    pieces: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        t = block['type']
        if t in ('bulleted_list_item', 'numbered_list_item'):
            tag = 'ul' if t == 'bulleted_list_item' else 'ol'
            items = []
            while i < len(blocks) and blocks[i]['type'] == t:
                items.append(render_list_item(blocks[i], pages))
                i += 1
            pieces.append(f'<{tag} class="notion-list">{"".join(items)}</{tag}>')
            continue
        pieces.append(render_block(block, pages))
        i += 1
    return ''.join(pieces)


def render_list_item(block: dict, pages: dict[str, dict]) -> str:
    text = render_inline_block(block)
    child = render_children(block.get('_children', []), pages)
    extra = f'<div class="nested-blocks">{child}</div>' if child else ''
    return f'<li>{text}{extra}</li>'


def render_inline_block(block: dict) -> str:
    t = block['type']
    payload = block.get(t, {})
    if t == 'paragraph':
        return rich_text_html(payload.get('rich_text', []))
    if t.startswith('heading_'):
        return rich_text_html(payload.get('rich_text', []))
    if t in ('bulleted_list_item', 'numbered_list_item', 'quote', 'callout', 'toggle', 'to_do'):
        return rich_text_html(payload.get('rich_text', []))
    if t == 'child_page':
        return html.escape(payload.get('title', 'Untitled page'))
    if t == 'code':
        code = html.escape(rich_text_plain(payload.get('rich_text', [])))
        lang = html.escape(payload.get('language', ''))
        return f'<div class="code-lang">{lang}</div><pre><code>{code}</code></pre>'
    if t == 'equation':
        return f'<code class="equation">{html.escape(payload.get("expression", ""))}</code>'
    return f'<span class="unsupported">[{html.escape(t)}]</span>'


def render_image(block: dict) -> str:
    payload = block.get('image', {})
    src = ''
    if payload.get('type') == 'external':
        src = payload.get('external', {}).get('url', '')
    elif payload.get('type') == 'file':
        src = payload.get('file', {}).get('url', '')
    if not src:
        return ''
    local = download_asset(src)
    return f'<img src="{html.escape(local.as_posix())}" alt="image">'


def render_router_script() -> str:
    return r"""(function () {
  const NAV_SELECTOR = '.sidebar .nav-tree';
  const CONTENT_SELECTOR = '.content';
  const INTERNAL_EXTENSIONS = ['.html'];
  const STORAGE_KEY = 'rocky-manual-sidebar-open-paths';
  const COLLAPSED_STORAGE_KEY = 'rocky-manual-sidebar-collapsed';
  const PAGE_DATA = window.ROCKY_MANUAL_PAGES || {};

  function readCollapsedState() {
    try {
      return localStorage.getItem(COLLAPSED_STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  }

  function writeCollapsedState(collapsed) {
    try {
      localStorage.setItem(COLLAPSED_STORAGE_KEY, collapsed ? '1' : '0');
    } catch {
      /* ignore storage errors */
    }
  }

  function syncCollapseButton(button, collapsed) {
    if (!button) return;
    button.textContent = '☰';
    button.title = collapsed ? '展開側欄' : '收合側欄';
    button.setAttribute('aria-label', collapsed ? '展開側欄' : '收合側欄');
    button.setAttribute('aria-expanded', String(!collapsed));
  }

  function applyCollapsedState(collapsed) {
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    syncCollapseButton(document.querySelector('.sidebar-toggle'), collapsed);
  }

  function toggleCollapsedState() {
    const collapsed = !document.body.classList.contains('sidebar-collapsed');
    writeCollapsedState(collapsed);
    applyCollapsedState(collapsed);
  }

  function bindSidebarToggle() {
    const button = document.querySelector('.sidebar-toggle');
    if (!button || button.dataset.sidebarToggleBound === '1') return;
    button.dataset.sidebarToggleBound = '1';
    button.addEventListener('click', (event) => {
      event.preventDefault();
      toggleCollapsedState();
    });
    applyCollapsedState(readCollapsedState());
  }

  function isModifiedClick(event) {
    return event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0;
  }

  function isInternalLink(anchor) {
    const href = anchor.getAttribute('href');
    if (!href || href.startsWith('#') || anchor.target === '_blank' || anchor.hasAttribute('download')) {
      return false;
    }
    const url = new URL(href, window.location.href);
    if (url.origin !== window.location.origin) {
      return false;
    }
    return INTERNAL_EXTENSIONS.some((ext) => url.pathname.toLowerCase().endsWith(ext));
  }

  function getDetailsSummaryText(details) {
    const summary = Array.from(details.children).find((el) => el.tagName === 'SUMMARY');
    return summary ? summary.textContent.trim().replace(/\s+/g, ' ') : '';
  }

  function getDetailsPath(details) {
    const parts = [];
    let node = details;
    while (node && node.tagName === 'DETAILS') {
      parts.push(getDetailsSummaryText(node));
      node = node.parentElement ? node.parentElement.closest('details') : null;
    }
    return parts.reverse().join(' > ');
  }

  function readStoredOpenPaths() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return new Set();
      const data = JSON.parse(raw);
      return new Set(Array.isArray(data) ? data : []);
    } catch {
      return new Set();
    }
  }

  function writeStoredOpenPaths(sidebar) {
    if (!sidebar) return;
    try {
      const openPaths = Array.from(sidebar.querySelectorAll('details[open]')).map(getDetailsPath);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(openPaths));
    } catch {
      /* ignore storage errors */
    }
  }

  function restoreStoredOpenPaths(sidebar) {
    if (!sidebar) return;
    const openPaths = readStoredOpenPaths();
    if (openPaths.size === 0) return;
    sidebar.querySelectorAll('details.nav-node').forEach((details) => {
      details.open = openPaths.has(getDetailsPath(details));
    });
  }

  function bindSidebarPersistence(sidebar) {
    if (!sidebar || sidebar.dataset.sidebarPersistenceBound === '1') return;
    sidebar.dataset.sidebarPersistenceBound = '1';
    sidebar.querySelectorAll('details.nav-node').forEach((details) => {
      details.addEventListener('toggle', () => writeStoredOpenPaths(sidebar));
    });
    restoreStoredOpenPaths(sidebar);
    writeStoredOpenPaths(sidebar);
  }

  function syncSidebarState(sidebar, sidebarHtml) {
    if (!sidebar) return;
    const template = document.createElement('div');
    template.innerHTML = sidebarHtml;

    sidebar.querySelectorAll('.active').forEach((el) => el.classList.remove('active'));

    template.querySelectorAll('.active').forEach((sourceEl) => {
      if (sourceEl.matches('a[href]')) {
        const href = sourceEl.getAttribute('href');
        const currentLink = Array.from(sidebar.querySelectorAll('a[href]')).find((link) => link.getAttribute('href') === href);
        if (currentLink) {
          currentLink.classList.add('active');
        }
        return;
      }

      if (sourceEl.matches('details.nav-node')) {
        const path = getDetailsPath(sourceEl);
        Array.from(sidebar.querySelectorAll('details.nav-node')).forEach((details) => {
          if (getDetailsPath(details) === path) {
            details.classList.add('active');
          }
        });
      }
    });

    bindSidebarPersistence(sidebar);
  }

  function rehydrateScripts(container) {
    container.querySelectorAll('script').forEach((oldScript) => {
      const script = document.createElement('script');
      for (const attr of oldScript.attributes) {
        script.setAttribute(attr.name, attr.value);
      }
      script.textContent = oldScript.textContent;
      oldScript.replaceWith(script);
    });
  }

  function resolveKey(url) {
    const pathname = new URL(url, window.location.href).pathname;
    const key = pathname.split('/').pop();
    return key || 'index.html';
  }

  function applyPageData(data, url, options = {}) {
    const sidebar = document.querySelector(NAV_SELECTOR);
    const content = document.querySelector(CONTENT_SELECTOR);
    if (!sidebar || !content) {
      window.location.href = url.toString();
      return;
    }

    const sidebarScrollTop = sidebar.scrollTop;
    syncSidebarState(sidebar, data.sidebarHtml);
    sidebar.scrollTop = sidebarScrollTop;

    content.innerHTML = data.contentHtml;
    rehydrateScripts(content);

    document.title = data.title;

    if (!options.replaceState) {
      window.history.pushState({ href: url.pathname + url.search + url.hash }, '', url.toString());
    } else {
      window.history.replaceState({ href: url.pathname + url.search + url.hash }, '', url.toString());
    }

    window.scrollTo({ top: 0, left: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
  }

  async function loadPage(href, options = {}) {
    const url = new URL(href, window.location.href);
    const key = resolveKey(url);
    const data = PAGE_DATA[key];

    if (data) {
      applyPageData(data, url, options);
      return;
    }

    const response = await fetch(url.toString(), { credentials: 'same-origin' });
    if (!response.ok) {
      throw new Error(`Failed to load ${url.toString()}: ${response.status}`);
    }

    const html = await response.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const nextSidebar = doc.querySelector(NAV_SELECTOR);
    const nextContent = doc.querySelector(CONTENT_SELECTOR);

    if (!nextSidebar || !nextContent) {
      window.location.href = url.toString();
      return;
    }

    applyPageData({
      title: doc.title,
      sidebarHtml: nextSidebar.innerHTML,
      contentHtml: nextContent.innerHTML,
    }, url, options);
  }

  bindSidebarToggle();
  bindSidebarPersistence(document.querySelector(NAV_SELECTOR));

  document.addEventListener('click', (event) => {
    if (isModifiedClick(event)) return;
    const anchor = event.target.closest('a[href]');
    if (!anchor || !isInternalLink(anchor)) return;
    event.preventDefault();
    loadPage(anchor.getAttribute('href')).catch(() => {
      window.location.href = anchor.href;
    });
  });

  window.addEventListener('popstate', () => {
    loadPage(window.location.href, { replaceState: true }).catch(() => {
      window.location.reload();
    });
  });
})();"""


def render_columns(block: dict, pages: dict[str, dict]) -> str:
    cols = []
    for col in block.get('_children', []):
        if col.get('type') != 'column':
            continue
        cols.append(f'<div class="column">{render_children(col.get("_children", []), pages)}</div>')
    return f'<div class="columns">{"".join(cols)}</div>'


def render_block(block: dict, pages: dict[str, dict]) -> str:
    t = block['type']
    if t == 'column_list':
        return render_columns(block, pages)
    if t == 'column':
        return ''
    if t in ('bulleted_list_item', 'numbered_list_item'):
        return render_list_item(block, pages)
    if t in ('heading_1', 'heading_2', 'heading_3', 'heading_4'):
        tag = {'heading_1': 'h1', 'heading_2': 'h2', 'heading_3': 'h3', 'heading_4': 'h4'}[t]
        return f'<{tag}>{render_inline_block(block)}</{tag}>'
    if t == 'paragraph':
        return f'<p>{render_inline_block(block)}</p>'
    if t == 'quote':
        return f'<blockquote>{render_inline_block(block)}</blockquote>'
    if t == 'callout':
        emoji = block.get('callout', {}).get('icon', {}).get('emoji', '💡')
        return f'<div class="callout"><span class="emoji">{html.escape(emoji)}</span><div>{render_inline_block(block)}</div></div>'
    if t == 'child_page':
        # Child pages are already represented in the sidebar tree.
        # Skip rendering them again in the body to avoid duplicate navigation items.
        return ''
    if t == 'image':
        return f'<figure class="image-block">{render_image(block)}</figure>'
    if t == 'divider':
        return '<hr>'
    if t == 'toggle':
        title = render_inline_block(block)
        return f'<details class="toggle"><summary>{title}</summary>{render_children(block.get("_children", []), pages)}</details>'
    if t == 'to_do':
        checked = 'checked' if block.get('to_do', {}).get('checked') else ''
        return f'<label class="todo"><input type="checkbox" disabled {checked}> {render_inline_block(block)}</label>{render_children(block.get("_children", []), pages)}'
    if t == 'code':
        return f'<div class="code-block">{render_inline_block(block)}</div>'
    return f'<div class="unsupported">[{html.escape(t)}]</div>'


def build_sidebar(pages: dict[str, dict], current_id: str) -> str:
    current_id = normalize_id(current_id)
    root_id = normalize_id(ROOT_PAGE_ID)

    parent: dict[str, str] = {}
    for pid, page in pages.items():
        for cid, _ in page.get('child_pages', []):
            parent[cid] = pid

    root_children = [cid for cid, _ in pages[root_id].get('child_pages', []) if cid in pages]

    path = {current_id}
    cur = current_id
    while cur in parent:
        cur = parent[cur]
        path.add(cur)

    def node(pid: str, top_level: bool = False) -> str:
        page = pages[pid]
        title = html.escape(page['title'])
        href = html.escape(page['filename'])
        child_ids = [cid for cid, _ in page.get('child_pages', []) if cid in pages]
        active = ' active' if pid in path else ''
        if child_ids:
            inner = ''.join(node(cid) for cid in child_ids)
            open_attr = ' open' if pid in path else ''
            summary_cls = 'section-summary' if top_level else 'nav-summary'
            label_cls = 'section-label' if top_level else 'nav-label'
            return (
                f'<details class="nav-node{active}"{open_attr}>'
                f'<summary class="{summary_cls}"><span class="{label_cls}">{title}</span></summary>'
                f'<div class="nav-children">{inner}</div>'
                f'</details>'
            )
        leaf_cls = 'nav-link leaf active' if pid == current_id else 'nav-link leaf'
        return f'<a class="{leaf_cls}" href="{href}">{title}</a>'

    if not root_children:
        child_html = '<div class="empty-nav">沒有可顯示的分區</div>'
    else:
        child_html = ''.join(node(cid, top_level=True) for cid in root_children)

    return f'<nav class="nav-tree">{child_html}</nav>'


def render_page(pages: dict[str, dict], page_id: str) -> str:
    page = pages[normalize_id(page_id)]
    if normalize_id(page_id) == normalize_id(SKILL_LIST_PAGE_ID):
        return render_skill_catalog_page(page_id, pages)
    sidebar = build_sidebar(pages, page_id)
    title = html.escape(page['title'])
    body = render_children(page['blocks'], pages)
    if normalize_id(page_id) == normalize_id(ROOT_PAGE_ID):
        body = body.replace('Notion', '文件')
    parent: dict[str, str] = {}
    for pid, info in pages.items():
        for cid, _ in info.get('child_pages', []):
            parent[cid] = pid
    chain = [normalize_id(page_id)]
    cur = normalize_id(page_id)
    while cur in parent:
        cur = parent[cur]
        chain.append(cur)
    chain.reverse()
    crumbs = []
    for cid in chain:
        if cid in pages:
            node = pages[cid]
            crumbs.append(f'<a href="{html.escape(node["filename"])}">{html.escape(node["title"])}</a>')
    breadcrumb_html = '<span class="sep">/</span>'.join(crumbs)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-head">
        <div class="brand">
          <a class="brand-link" href="index.html" aria-label="回到首頁">
            <img class="brand-icon" src="assets/media/rocky-home-icon.jpeg" alt="Rocky 使用指南" />
          </a>
          <div>
            <div class="brand-title">Rocky 使用指南</div>
          </div>
        </div>
        <button class="sidebar-toggle" type="button" aria-expanded="true" aria-label="收合側欄" title="收合側欄">☰</button>
      </div>
      <a class="sidebar-home" href="index.html">指南首頁</a>
      <nav class="nav-tree">{sidebar}</nav>
    </aside>
    <main class="content">
      <div class="topbar">
        <div class="breadcrumbs">{breadcrumb_html}</div>
        <div class="page-id">{short_id(page_id)}</div>
      </div>
      <article class="page-card">
        <header class="page-header">
          <h1>{title}</h1>
        </header>
        <section class="page-body">{body}</section>
      </article>
    </main>
  </div>
  <script src="assets/pages.js"></script>
  <script src="assets/router.js"></script>
</body>
</html>"""



def build_assets() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if BRAND_ICON_SOURCE.exists():
        shutil.copyfile(BRAND_ICON_SOURCE, MEDIA_DIR / BRAND_ICON_SOURCE.name)
    (ASSET_DIR / 'router.js').write_text(render_router_script(), encoding='utf-8')
    css = """
:root {
  color-scheme: dark;
  --bg: #0b1020;
  --line: rgba(148, 163, 184, 0.18);
  --text: #e5eefc;
  --muted: #94a3b8;
  --accent: #7dd3fc;
  --shadow: 0 20px 60px rgba(0, 0, 0, 0.32);
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: radial-gradient(circle at top left, #15203a 0, #0b1020 34%, #090d17 100%); color: var(--text); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.app-shell { display: grid; grid-template-columns: 320px 1fr; min-height: 100vh; transition: grid-template-columns .2s ease; }
.sidebar { position: sticky; top: 0; height: 100vh; overflow: auto; background: linear-gradient(180deg, rgba(8,12,24,.95), rgba(9,14,28,.88)); border-right: 1px solid var(--line); padding: 20px 16px; backdrop-filter: blur(16px); transition: padding .2s ease; }
.sidebar-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 20px; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand-link { display: inline-flex; flex: 0 0 auto; border-radius: 16px; }
.brand-icon { width: 48px; height: 48px; border-radius: 16px; display: block; object-fit: cover; box-shadow: var(--shadow); border: 1px solid rgba(255,255,255,0.10); }
.brand-title { font-weight: 700; letter-spacing: .2px; }
.sidebar-toggle { flex: 0 0 auto; width: 40px; height: 40px; border-radius: 12px; border: 1px solid rgba(125,211,252,.18); background: rgba(5, 8, 22, 0.84); color: var(--text); font-size: 18px; line-height: 1; cursor: pointer; transition: transform .15s ease, border-color .15s ease, background .15s ease; }
.sidebar-toggle:hover { transform: translateY(-1px); border-color: rgba(125,211,252,.42); background: rgba(125,211,252,.10); }
.sidebar-home { display: inline-flex; align-items: center; justify-content: center; margin: 16px 0 18px; padding: 10px 14px; border-radius: 14px; border: 1px solid rgba(125,211,252,.22); background: linear-gradient(180deg, rgba(125,211,252,.18), rgba(125,211,252,.08)); color: var(--text); font-weight: 700; box-shadow: inset 0 1px 0 rgba(255,255,255,.03); }
body.sidebar-collapsed .app-shell { grid-template-columns: 72px 1fr; }
body.sidebar-collapsed .sidebar { padding: 16px 10px; }
body.sidebar-collapsed .sidebar-head { justify-content: center; }
body.sidebar-collapsed .brand { display: none; }
body.sidebar-collapsed .brand-title,
body.sidebar-collapsed .sidebar-home,
body.sidebar-collapsed .nav-tree,
body.sidebar-collapsed .sidebar-page-title,
body.sidebar-collapsed .parent-link,
body.sidebar-collapsed .empty-nav { display: none; }
body.sidebar-collapsed .brand { justify-content: center; }
body.sidebar-collapsed .brand-link { margin: 0; }
body.sidebar-collapsed .sidebar-toggle { margin-left: 0; }
.sidebar-current { font-size: 18px; font-weight: 700; line-height: 1.2; margin-top: 8px; }
.parent-link { display: inline-block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.empty-nav { color: var(--muted); font-size: 13px; padding: 8px 2px; }
.section-summary, .nav-summary { list-style: none; cursor: pointer; display: block; padding: 9px 12px; border-radius: 12px; color: var(--text); }
.section-summary::-webkit-details-marker, .nav-summary::-webkit-details-marker { display: none; }
.section-summary { font-weight: 700; font-size: 15px; }
.section-summary:hover, .nav-summary:hover { background: rgba(125, 211, 252, 0.10); }
.nav-label, .section-label { display: block; }
.nav-tree { font-size: 14px; }
.nav-node { margin: 4px 0; }
.nav-link { display: block; padding: 9px 12px; border-radius: 12px; color: var(--text); background: transparent; }
.nav-link:hover { background: rgba(125, 211, 252, 0.10); text-decoration: none; }
.nav-link.active, .nav-node.active > .section-summary, .nav-node.active > .nav-summary { background: linear-gradient(90deg, rgba(125,211,252,0.18), rgba(167,139,250,0.16)); border: 1px solid rgba(125,211,252,0.28); }
.nav-link.leaf { margin-left: 4px; }
.nav-children { margin-left: 14px; padding-left: 10px; border-left: 1px dashed rgba(148,163,184,0.18); }
.content { padding: 28px; overflow: auto; }
.topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; color: var(--muted); font-size: 13px; }
.breadcrumbs a { color: var(--muted); }
.sep { margin: 0 8px; color: rgba(148,163,184,.5); }
.page-id { border: 1px solid var(--line); padding: 6px 10px; border-radius: 999px; background: rgba(15,23,42,.7); }
.page-card { max-width: 980px; margin: 0 auto; background: linear-gradient(180deg, rgba(15,23,42,.80), rgba(15,23,42,.60)); border: 1px solid var(--line); border-radius: 26px; box-shadow: var(--shadow); overflow: hidden; }
.page-header { padding: 32px 34px 18px; border-bottom: 1px solid var(--line); background: linear-gradient(135deg, rgba(56,189,248,0.08), rgba(167,139,250,0.10)); }
.page-header h1 { margin: 0; font-size: clamp(30px, 4vw, 48px); line-height: 1.06; letter-spacing: -.03em; }
.page-meta { margin-top: 10px; color: var(--muted); font-size: 13px; }
.page-body { padding: 26px 34px 38px; font-size: 16px; line-height: 1.8; }
.page-body h2, .page-body h3, .page-body h4 { margin: 28px 0 12px; line-height: 1.2; }
.page-body h2 { font-size: 28px; }
.page-body h3 { font-size: 22px; }
.page-body h4 { font-size: 18px; }
.page-body p { margin: 12px 0; color: #d8e3f4; }
.page-body blockquote { margin: 18px 0; padding: 14px 18px; border-left: 4px solid var(--accent); background: rgba(125, 211, 252, 0.08); border-radius: 14px; color: #eef6ff; }
.page-body hr { border: 0; border-top: 1px solid var(--line); margin: 26px 0; }
.page-body ul.notion-list, .page-body ol.notion-list { padding-left: 24px; margin: 12px 0; }
.page-body li { margin: 8px 0; }
.nested-blocks { margin-top: 8px; margin-bottom: 4px; }
.callout { display: flex; gap: 14px; align-items: flex-start; padding: 16px 18px; border: 1px solid rgba(125,211,252,.18); background: rgba(125,211,252,.07); border-radius: 18px; margin: 16px 0; }
.callout .emoji { font-size: 20px; }
.todo { display: inline-flex; align-items: center; gap: 10px; }
.code-block, pre { background: #050816; border: 1px solid rgba(148,163,184,.16); border-radius: 16px; overflow: auto; }
pre { padding: 16px 18px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .95em; }
.code-lang { color: var(--muted); font-size: 12px; margin: 0 0 8px 2px; }
.image-block { margin: 18px 0; }
.image-block img { max-width: 100%; border-radius: 18px; border: 1px solid var(--line); box-shadow: var(--shadow); }
.columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin: 18px 0; }
.column { min-width: 0; }
.child-page-card { display: block; margin: 12px 0; padding: 16px 18px; border-radius: 18px; border: 1px solid rgba(167,139,250,.18); background: rgba(167,139,250,.08); }
.child-page-card span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.child-page-card strong { color: var(--text); font-size: 16px; }
.toggle { margin: 14px 0; }
.skill-browser { display: grid; gap: 18px; }
.skill-intro { margin: 0; color: var(--muted); font-size: 15px; }
.skill-toolbar { display: grid; grid-template-columns: 1.6fr 0.9fr auto; gap: 14px; align-items: end; padding: 18px; border: 1px solid var(--line); border-radius: 20px; background: rgba(15, 23, 42, 0.62); box-shadow: inset 0 1px 0 rgba(255,255,255,0.03); }
.skill-search, .skill-filter { display: grid; gap: 8px; }
.skill-search span, .skill-filter span { font-size: 12px; color: var(--muted); letter-spacing: .04em; text-transform: uppercase; }
.skill-tag-filters { display: flex; flex-wrap: wrap; gap: 8px; }
.skill-tag-filter { border: 1px solid rgba(148,163,184,.22); background: rgba(5, 8, 22, 0.82); color: var(--text); border-radius: 999px; padding: 8px 12px; font: inherit; font-size: 13px; cursor: pointer; transition: all .15s ease; }
.skill-tag-filter span { color: inherit; font-size: inherit; text-transform: none; letter-spacing: 0; }
.skill-tag-filter:hover { border-color: rgba(125,211,252,.42); transform: translateY(-1px); }
.skill-tag-filter-active { background: linear-gradient(180deg, rgba(125,211,252,.22), rgba(167,139,250,.18)); border-color: rgba(125,211,252,.48); box-shadow: 0 0 0 2px rgba(125,211,252,.08) inset; }
.skill-search input { width: 100%; border: 1px solid rgba(148,163,184,.24); background: rgba(5, 8, 22, 0.92); color: var(--text); border-radius: 14px; padding: 13px 14px; outline: none; font: inherit; }
.skill-search input:focus { border-color: rgba(125,211,252,.65); box-shadow: 0 0 0 3px rgba(125,211,252,.12); }
.skill-reset { align-self: end; border: 1px solid rgba(125,211,252,.22); background: linear-gradient(180deg, rgba(125,211,252,.18), rgba(125,211,252,.08)); color: var(--text); border-radius: 14px; padding: 12px 16px; font: inherit; cursor: pointer; transition: transform .15s ease, border-color .15s ease; }
.skill-reset:hover { transform: translateY(-1px); border-color: rgba(125,211,252,.42); }
.skill-stats { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; flex-wrap: wrap; }
.skill-stats strong, .skill-stats #skill-visible-count { color: var(--text); font-weight: 700; }
.skill-stats-sep { color: rgba(148,163,184,.55); }
.skill-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.skill-card { border: 1px solid rgba(148,163,184,.16); border-radius: 20px; padding: 18px; background: linear-gradient(180deg, rgba(10, 16, 32, 0.96), rgba(10, 16, 32, 0.72)); box-shadow: var(--shadow); display: grid; gap: 12px; }
.skill-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.skill-card h3 { margin: 0; font-size: 18px; line-height: 1.2; }
.skill-category { flex: 0 0 auto; padding: 5px 10px; border-radius: 999px; background: rgba(125,211,252,.12); border: 1px solid rgba(125,211,252,.18); color: #dff5ff; font-size: 12px; }
.skill-desc { margin: 0; color: #d8e3f4; line-height: 1.7; }
.skill-tags { display: flex; flex-wrap: wrap; gap: 8px; }
.skill-tag, .skill-tag-empty { display: inline-flex; align-items: center; padding: 5px 10px; border-radius: 999px; font-size: 12px; line-height: 1; }
.skill-tag { background: rgba(167,139,250,.14); color: #efe7ff; border: 1px solid rgba(167,139,250,.22); }
.skill-tag-empty { background: rgba(148,163,184,.10); color: var(--muted); border: 1px dashed rgba(148,163,184,.18); }
.skill-empty { margin: 0; padding: 18px; border: 1px dashed rgba(125,211,252,.22); border-radius: 18px; color: var(--muted); background: rgba(125,211,252,.05); }
@media (max-width: 980px) {
  .app-shell { grid-template-columns: 1fr; }
  .sidebar { position: relative; height: auto; }
  .content { padding: 16px; }
}
"""
    (ASSET_DIR / 'style.css').write_text(css, encoding='utf-8')


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_assets()
    pages = discover_pages(ROOT_PAGE_ID)
    page_assets = {
        page['filename']: {
            'title': page['title'],
            'sidebarHtml': build_sidebar(pages, pid).split('<nav class="nav-tree">', 1)[1].rsplit('</nav>', 1)[0],
            'contentHtml': render_page(pages, pid).split('<main class="content">', 1)[1].split('</main>', 1)[0],
        }
        for pid, page in pages.items()
    }
    (OUTPUT_DIR / 'assets' / 'pages.js').write_text('window.ROCKY_MANUAL_PAGES = ' + json.dumps(page_assets, ensure_ascii=False) + ';\n', encoding='utf-8')
    for pid, page in pages.items():
        (OUTPUT_DIR / page['filename']).write_text(render_page(pages, pid), encoding='utf-8')
    manifest = {
        'root_page_id': normalize_id(ROOT_PAGE_ID),
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'pages': [
            {
                'id': pid,
                'title': page['title'],
                'filename': page['filename'],
                'child_pages': [{'id': cid, 'title': title} for cid, title in page.get('child_pages', [])],
            }
            for pid, page in pages.items()
        ],
    }
    (OUTPUT_DIR / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Generated {len(pages)} pages into {OUTPUT_DIR}')


if __name__ == '__main__':
    main()
