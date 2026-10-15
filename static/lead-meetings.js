(() => {
  'use strict';
  const section = document.getElementById('lead-meetings');
  if (!section) return;
  const form = section.querySelector('form');
  const opener = section.querySelector('[data-open-meeting]');
  const error = section.querySelector('.meeting-error');
  const feedback = section.querySelector('.meeting-feedback');
  const submit = form.querySelector('[type="submit"]');
  let requestId = null;
  let busy = false;
  function showForm(open) {
    form.hidden = !open;
    opener.setAttribute('aria-expanded', String(open));
    if (open) {
      requestId = requestId || crypto.randomUUID();
      form.querySelector('select').focus();
    } else opener.focus();
  }
  opener.addEventListener('click', () => showForm(form.hidden));
  section.querySelector('[data-close-meeting]').addEventListener('click', () => showForm(false));
  form.elements.meeting_kind.addEventListener('change', () => {
    const virtual = form.elements.meeting_kind.value === 'virtual';
    section.querySelector('[data-virtual-field]').hidden = !virtual;
    section.querySelector('[data-presencial-field]').hidden = virtual;
    form.elements.meeting_url.disabled = !virtual;
    form.elements.meeting_url.required = virtual;
    form.elements.location.disabled = virtual;
    form.elements.location.required = !virtual;
  });
  section.querySelectorAll('[data-retry-meeting]').forEach(button => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const response = await fetch(`/api/leads/${encodeURIComponent(section.dataset.leadId)}/meetings/${encodeURIComponent(button.dataset.retryMeeting)}/invite`, {method: 'POST'});
        const result = await response.json();
        if (!response.ok || !result.ok || result.delivery_status === 'unprepared') throw new Error('No se pudo preparar la invitación. Intenta de nuevo.');
        window.location.hash = 'lead-meetings';
        window.location.reload();
      } catch (problem) {
        feedback.textContent = problem.message;
        feedback.hidden = false;
        feedback.focus();
        button.disabled = false;
      }
    });
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    busy = true;
    submit.disabled = true;
    submit.textContent = 'Guardando y enviando…';
    error.hidden = true;
    // Keep the same request key after a network failure: saving twice must not send twice.
    const payload = Object.fromEntries(new FormData(form));
    payload.request_id = requestId;
    try {
      const response = await fetch(`/api/leads/${encodeURIComponent(section.dataset.leadId)}/meetings`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'No se pudo guardar la reunión. Intenta de nuevo.');
      if (result.delivery_status === 'unprepared') {
        throw new Error('La reunión se guardó, pero no se pudo preparar la invitación. Pulsa Guardar y enviar invitación para reintentar.');
      }
      window.location.hash = 'lead-meetings';
      window.location.reload();
    } catch (problem) {
      error.textContent = problem.message || 'Revisa tu conexión e intenta de nuevo. La misma reunión no se duplicará.';
      error.hidden = false;
    } finally {
      busy = false;
      submit.disabled = false;
      submit.textContent = 'Guardar y enviar invitación';
    }
  });
})();
