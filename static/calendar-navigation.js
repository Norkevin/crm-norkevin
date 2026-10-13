/* Update calendar fragments while retaining the toolbar, focus and scroll position. */
(() => {
  const root = document.querySelector('[data-calendar-root]');
  if (!root || !window.fetch) return;
  const content = root.querySelector('[data-calendar-content]');
  const status = root.querySelector('[data-calendar-status]');
  const cache = new Map();
  const cacheTTL = 30000;
  let revision = 0, warmTimer;
  const keyFor = value => { const url = new URL(value, location.href); url.hash = ''; return url.href; };
  const selector = `[data-calendar-root="${root.dataset.calendarRoot}"]`;

  async function load(value) {
    const key = keyFor(value);
    const cached = cache.get(key);
    if (cached && Date.now() - cached.time < cacheTTL) return cached.promise;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    const promise = (async () => {
      try {
        const response = await fetch(key, {credentials:'same-origin', cache:'no-store',
          headers:{'X-Calendar-Fragment':'1'}, signal:controller.signal});
        if (response.status === 401 || response.status === 403 || response.redirected) {
          const error = new Error('Tu sesión necesita revisión.');
          error.loginURL = response.redirected ? response.url : key;
          throw error;
        }
        if (!response.ok) throw new Error('No se pudo cargar el calendario.');
        const incoming = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector(selector);
        if (!incoming?.querySelector('[data-calendar-content]')) throw new Error('No se pudo cargar el calendario.');
        return incoming;
      } catch (error) { cache.delete(key); throw error; }
      finally { clearTimeout(timeout); }
    })();
    cache.set(key, {time:Date.now(), promise});
    if (cache.size > 6) cache.delete(cache.keys().next().value);
    return promise;
  }

  const warm = () => {
    clearTimeout(warmTimer);
    warmTimer = setTimeout(() => root.querySelectorAll('[data-calendar-control="previous"], [data-calendar-control="next"]')
      .forEach(link => load(link.href).catch(() => {})), 150);
  };

  async function navigate(value, push = true) {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin || url.pathname !== location.pathname) return;
    const current = ++revision;
    root.setAttribute('aria-busy', 'true');
    status.textContent = 'Cargando calendario…';
    status.classList.remove('calendar-navigation-error');
    try {
      const incoming = await load(url.href);
      if (current !== revision || !root.isConnected) return;
      const scroll = [];
      for (let node = root.parentElement; node; node = node.parentElement) scroll.push([node, node.scrollTop, node.scrollLeft]);
      const focusInContent = content.contains(document.activeElement);
      content.style.minHeight = `${content.getBoundingClientRect().height}px`;
      root.querySelectorAll('[data-calendar-control]').forEach(control => {
        const next = incoming.querySelector(`[data-calendar-control="${control.dataset.calendarControl}"]`);
        if (next) control.href = new URL(next.getAttribute('href'), location.href).href;
      });
      root.querySelector('[data-calendar-label]').textContent = incoming.querySelector('[data-calendar-label]').textContent;
      const month = root.querySelector('input[name="month"]');
      if (month) month.value = incoming.querySelector('input[name="month"]').value;
      content.innerHTML = incoming.querySelector('[data-calendar-content]').innerHTML;
      if (root.dataset.calendarRoot === 'owner') window.filterCalendar?.();
      if (push) history.pushState({...history.state, flowCalendar:true}, '', url.href);
      scroll.forEach(([node, top, left]) => { node.scrollTop = top; node.scrollLeft = left; });
      if (focusInContent) (content.querySelector('[data-calendar-selection]') || root.querySelector('[data-calendar-label]')).focus({preventScroll:true});
      if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches)
        content.animate?.([{opacity:0.65}, {opacity:1}], {duration:140, easing:'ease-out'});
      status.textContent = '';
      warm();
    } catch (error) {
      if (current !== revision) return;
      if (error.loginURL && new URL(error.loginURL, location.href).origin === location.origin) {
        location.assign(error.loginURL); return;
      }
      status.textContent = 'No se pudo cargar el mes. Vuelve a pulsar la flecha o «Ir» para reintentar.';
      status.classList.add('calendar-navigation-error');
    } finally {
      if (current === revision) root.setAttribute('aria-busy', 'false');
    }
  }

  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-calendar-link]');
    if (!link || !root.contains(link) || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(link.href);
  });
  document.addEventListener('submit', event => {
    if (!event.target.matches('[data-calendar-form]') || !root.contains(event.target)) return;
    event.preventDefault();
    const url = new URL(event.target.action || location.href);
    url.search = new URLSearchParams(new FormData(event.target)).toString();
    url.hash = '';
    navigate(url.href);
  });
  window.addEventListener('popstate', () => {
    if (history.state?.flowCalendar) navigate(location.href, false);
  });
  history.replaceState({...history.state, flowCalendar:true}, '', location.href);
  cache.set(keyFor(location.href), {time:Date.now(), promise:Promise.resolve(root.cloneNode(true))});
  warm();
})();
