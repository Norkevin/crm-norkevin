// Native disclosures keep secondary information accessible without long mobile headers.
(function () {
  // iOS scroll containers can clip fixed dialogs; overlays belong at the root.
  document.querySelectorAll('.main-content .modal-overlay').forEach(function (modal) {
    document.body.appendChild(modal);
  });
  var mobile = window.matchMedia('(max-width: 760px)');
  function sync() {
    document.querySelectorAll('[data-mobile-fold]').forEach(function (details) {
      details.open = !mobile.matches;
    });
  }
  sync();
  mobile.addEventListener('change', sync);
})();
