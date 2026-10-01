/* Absolute timestamps keep the countdown correct in any browser timezone. */
(function () {
  function remaining(due, now) {
    var minutes = Math.ceil((due - now) / 60000);
    if (minutes <= 0) return 'Plazo cumplido. Pendiente de preparación automática.';
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor((minutes % 1440) / 60);
    var rest = minutes % 60;
    var parts = [];
    if (days) parts.push(days + (days === 1 ? ' día' : ' días'));
    if (hours) parts.push(hours + (hours === 1 ? ' hora' : ' horas'));
    if (!days && rest) parts.push(rest + (rest === 1 ? ' minuto' : ' minutos'));
    return 'Preparación automática en ' + parts.join(' y ') + '.';
  }
  function update() {
    document.querySelectorAll('[data-workflow-due]').forEach(function (element) {
      var due = Number(element.dataset.workflowDue);
      if (Number.isFinite(due)) element.textContent = remaining(due, Date.now());
    });
  }
  update();
  setInterval(update, 30000);
})();
