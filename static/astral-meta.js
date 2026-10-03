// Astral public forms only. Explicit events; no automatic field collection.
!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}
(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('set', 'autoConfig', false, '28915845924706844');
fbq('init', '28915845924706844');
fbq('trackSingle', '28915845924706844', 'PageView');

// Preserve actual ad clicks when visiting the Astral form on FLOW.
(() => {
  if (window.location.hostname !== 'astralfilmsgt.com') return;
  const valid = value => /^[A-Za-z0-9_-]{1,1024}$/.test(value || '');
  let clickId = new URLSearchParams(window.location.search).get('fbclid');
  try {
    if (valid(clickId)) sessionStorage.setItem('astral_fbclid', clickId);
    else clickId = sessionStorage.getItem('astral_fbclid');
  } catch (_) { /* Restricted storage must not break the form link. */ }
  if (!valid(clickId)) return;
  document.querySelectorAll('a[href]').forEach(link => {
    const url = new URL(link.href);
    if (url.origin !== 'https://flowingcrm.com' || url.pathname !== '/captacion/astral-weddings') return;
    url.searchParams.set('fbclid', clickId);
    link.href = url.href;
  });
})();
