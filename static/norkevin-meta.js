// Norkevin public pages only. No automatic form-field or click collection.
!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}
(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('set', 'autoConfig', false, '899434420809998');
fbq('init', '899434420809998');
fbq('trackSingle', '899434420809998', 'PageView');

// Carry the actual Meta ad click to the contact form across our two domains.
// Session-only retention; never invent an identifier for an organic visitor.
(() => {
  if (window.location.hostname !== 'norkevinweddings.com') return;
  const valid = value => /^[A-Za-z0-9_-]{1,1024}$/.test(value || '');
  let clickId = new URLSearchParams(window.location.search).get('fbclid');
  try {
    if (valid(clickId)) sessionStorage.setItem('norkevin_fbclid', clickId);
    else clickId = sessionStorage.getItem('norkevin_fbclid');
  } catch (_) { /* Storage restrictions must not break contact links. */ }
  if (!valid(clickId)) return;
  document.querySelectorAll('a[href]').forEach(link => {
    const url = new URL(link.href);
    if (url.origin !== 'https://flowingcrm.com' || url.pathname !== '/captacion/norkevin-photography') return;
    url.searchParams.set('fbclid', clickId);
    link.href = url.href;
  });
})();
