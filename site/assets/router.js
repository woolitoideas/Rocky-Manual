(function () {
  const NAV_SELECTOR = '.sidebar .nav-tree';
  const CONTENT_SELECTOR = '.content';
  const INTERNAL_EXTENSIONS = ['.html'];
  const PAGE_DATA = window.ROCKY_MANUAL_PAGES || {};

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
    sidebar.innerHTML = data.sidebarHtml;
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