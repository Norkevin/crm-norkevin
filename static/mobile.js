// Native disclosures keep secondary information accessible without long mobile headers.
(function () {
  var mobile = window.matchMedia('(max-width: 760px)');
  function sync() {
    document.querySelectorAll('[data-mobile-fold]').forEach(function (details) {
      details.open = !mobile.matches;
    });
  }
  sync();
  mobile.addEventListener('change', sync);
})();
