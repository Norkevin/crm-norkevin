(() => {
  const form = document.querySelector('[data-portal-login]');
  if (!form) return;
  const params = new URLSearchParams(location.hash.slice(1));
  const access = params.get('invite') || params.get('access');
  if (access) {
    const field = form.elements.code;
    field.value = access;
    field.type = 'hidden';
    field.closest('label').hidden = true;
    history.replaceState(null, '', location.pathname + location.search);
  }
  if (access || form.dataset.autoLogin === 'true') form.requestSubmit();
})();
