// Normal document navigation retains each tab's position, without caching pages.
(function () {
  var main = document.querySelector('.main-content');
  var nav = document.querySelector('.bottom-nav');
  if (!main || !nav) return;
  var mobile = window.matchMedia('(max-width: 760px)');
  var path = window.location.pathname;
  var key = 'flowcrm-tab-scroll:' + path;
  var isTab = ['/dashboard', '/leads', '/clients', '/jobs'].includes(path);

  function savePosition() {
    if (!mobile.matches || !isTab) return;
    try { sessionStorage.setItem(key, String(main.scrollTop)); } catch (_) {}
  }
  function clearPending() {
    nav.querySelectorAll('.is-pending').forEach(function (link) {
      link.classList.remove('is-pending');
      link.removeAttribute('aria-busy');
    });
  }
  if (mobile.matches && isTab) {
    try { main.scrollTop = Number(sessionStorage.getItem(key)) || 0; } catch (_) {}
  }
  nav.addEventListener('click', function (event) {
    var link = event.target.closest('a[href]');
    if (!mobile.matches || !link || event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var target = new URL(link.href, window.location.href);
    if (target.origin !== window.location.origin) return;
    if (target.href === window.location.href) {
      event.preventDefault();
      main.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
      try { sessionStorage.removeItem(key); } catch (_) {}
      return;
    }
    savePosition();
    clearPending();
    link.classList.add('is-pending');
    link.setAttribute('aria-busy', 'true');
  });
  window.addEventListener('pagehide', savePosition);
  window.addEventListener('pageshow', clearPending);
})();
