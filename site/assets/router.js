(function () {
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
})();