/* Absolute timestamps keep the countdown correct in any browser timezone. */
(function () {
  var refreshing = false;
  function remaining(due, now, automatic) {
    var minutes = Math.ceil((due - now) / 60000);
    if (minutes <= 0) return automatic ? 'Plazo cumplido. Esperando el envío automático.' : 'Plazo cumplido. Pendiente de preparación automática.';
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor((minutes % 1440) / 60);
    var rest = minutes % 60;
    var parts = [];
    if (days) parts.push(days + (days === 1 ? ' día' : ' días'));
    if (hours) parts.push(hours + (hours === 1 ? ' hora' : ' horas'));
    if (!days && rest) parts.push(rest + (rest === 1 ? ' minuto' : ' minutos'));
    return (automatic ? 'Envío automático en ' : 'Preparación automática en ') + parts.join(' y ') + '.';
  }
  function update(refreshAllowed) {
    var waiting = false;
    document.querySelectorAll('[data-workflow-due]').forEach(function (element) {
      var due = Number(element.dataset.workflowDue);
      var automatic = element.dataset.workflowAuto === '1';
      if (Number.isFinite(due)) {
        element.textContent = remaining(due, Date.now(), automatic);
        if (automatic && due <= Date.now()) waiting = true;
      }
    });
    if (refreshAllowed !== false && (waiting || document.querySelector('[data-workflow-watch="1"]'))) refresh();
  }
  async function refresh() {
    if (refreshing || document.hidden || !document.querySelector('[data-workflow-live]') ||
        document.querySelector('.modal-overlay.open, .sn-action-menu')) return;
    refreshing = true;
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 10000);
    try {
      var response = await fetch(window.location.pathname, {cache: 'no-store', signal: controller.signal});
      if (!response.ok || response.redirected) return;
      var fresh = new DOMParser().parseFromString(await response.text(), 'text/html');
      document.querySelectorAll('[data-workflow-live]').forEach(function (panel) {
        // Keep unsaved notes, open dialogs and focused workflow controls intact.
        if (panel.contains(document.activeElement)) return;
        var replacement = fresh.querySelector('[data-workflow-live="' + panel.dataset.workflowLive + '"]');
        if (!replacement) return;
        var details = replacement.querySelectorAll('details');
        panel.querySelectorAll('details').forEach(function (item, index) {
          if (details[index]) details[index].open = item.open;
        });
        panel.replaceWith(replacement);
      });
      update(false);
    } catch (_) {
      // The next visible tick retries; a network failure never claims delivery.
    } finally {
      clearTimeout(timer);
      refreshing = false;
    }
  }
  update();
  setInterval(update, 30000);
})();
