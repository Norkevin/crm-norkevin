(() => {
  'use strict';
  const section = document.getElementById('lead-meetings');
  if (!section) return;
  const meetings = new Map(JSON.parse(document.getElementById('lead-meeting-data').textContent).map(meeting => [meeting.id, meeting]));
  const form = section.querySelector('form');
  const fields = form.querySelector('.meeting-fields');
  const opener = section.querySelector('[data-open-meeting]');
  const closer = section.querySelector('[data-close-meeting]');
  const error = section.querySelector('.meeting-error');
  const feedback = section.querySelector('.meeting-feedback');
  const submit = form.querySelector('[type="submit"]');
  const base = `/api/leads/${encodeURIComponent(section.dataset.leadId)}/meetings`;
  const defaultRecipient = document.getElementById('meeting-recipient').textContent;
  let requestId;
  let action = 'create';
  let selected = null;
  let activeButton = opener;
  let busy = false;
  const submitLabel = () => action === 'cancel' ? 'Confirmar cancelación y avisar' : action === 'update' ? 'Guardar cambios y avisar' : 'Guardar y enviar invitación';
  function syncFields() {
    const cancel = action === 'cancel';
    fields.hidden = cancel;
    fields.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = cancel || busy; });
    const virtual = form.elements.meeting_kind.value === 'virtual';
    section.querySelector('[data-virtual-field]').hidden = !virtual;
    section.querySelector('[data-presencial-field]').hidden = virtual;
    form.elements.meeting_url.disabled = cancel || busy || !virtual;
    form.elements.meeting_url.required = virtual;
    form.elements.location.disabled = cancel || busy || virtual;
    form.elements.location.required = !virtual;
    form.querySelector('.meeting-timezone').hidden = cancel;
  }
  function setBusy(value) {
    busy = value;
    section.querySelectorAll('button').forEach(button => { button.disabled = value; });
    syncFields();
    submit.textContent = value ? (action === 'cancel' ? 'Cancelando y avisando…' : 'Guardando y enviando…') : submitLabel();
  }
  function closeForm() {
    if (busy) return;
    form.hidden = true;
    activeButton.setAttribute('aria-expanded', 'false');
    activeButton.focus();
  }
  function openForm(mode, meeting, button) {
    if (busy) return;
    activeButton.setAttribute('aria-expanded', 'false');
    action = mode;
    selected = meeting || null;
    activeButton = button;
    activeButton.setAttribute('aria-expanded', 'true');
    requestId = crypto.randomUUID();
    form.reset();
    error.hidden = true;
    form.hidden = false;
    form.classList.toggle('is-cancelling', action === 'cancel');
    document.getElementById('meeting-form-title').textContent = action === 'cancel' ? 'Cancelar reunión' : action === 'update' ? 'Cambiar fecha de la reunión' : 'Nueva reunión';
    document.getElementById('meeting-action-description').textContent = action === 'cancel' ? 'Cancelaremos la cita y enviaremos el aviso a' : action === 'update' ? 'Guardaremos los cambios y enviaremos el aviso a' : 'Al guardar, enviaremos la invitación a';
    document.getElementById('meeting-recipient').textContent = selected ? selected.recipient : defaultRecipient;
    const target = form.querySelector('.meeting-target');
    target.hidden = !selected;
    if (selected) {
      for (const name of ['meeting_kind', 'date', 'start_time', 'end_time', 'meeting_url', 'location', 'notes']) form.elements[name].value = selected[name] || '';
      target.textContent = button.closest('li').querySelector('.meeting-when').innerText.replace(/\n/g, ' · ');
    }
    form.querySelector('.meeting-cancel-help').hidden = action !== 'cancel';
    closer.textContent = action === 'cancel' ? 'Volver' : 'Cancelar';
    submit.textContent = submitLabel();
    syncFields();
    (action === 'cancel' ? closer : selected ? form.elements.date : form.elements.meeting_kind).focus();
  }
  opener.addEventListener('click', () => !form.hidden && action === 'create' ? closeForm() : openForm('create', null, opener));
  closer.addEventListener('click', closeForm);
  form.elements.meeting_kind.addEventListener('change', syncFields);
  section.querySelectorAll('[data-edit-meeting]').forEach(button => button.addEventListener('click', () => openForm('update', meetings.get(button.dataset.editMeeting), button)));
  section.querySelectorAll('[data-cancel-meeting]').forEach(button => button.addEventListener('click', () => openForm('cancel', meetings.get(button.dataset.cancelMeeting), button)));
  section.querySelectorAll('[data-retry-meeting]').forEach(button => {
    button.addEventListener('click', async () => {
      if (busy) return;
      setBusy(true);
      try {
        const meeting = meetings.get(button.dataset.retryMeeting);
        const response = await fetch(`${base}/${encodeURIComponent(meeting.id)}/invite`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({version: meeting.version})});
        const result = await response.json();
        if (!response.ok || !result.ok || result.delivery_status === 'unprepared') throw new Error(result.error || 'No se pudo preparar el aviso. Intenta de nuevo.');
        window.location.hash = 'lead-meetings';
        window.location.reload();
      } catch (problem) {
        feedback.textContent = problem.message;
        feedback.hidden = false;
        feedback.focus();
        setBusy(false);
      }
    });
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    // Keep the same key after a network failure; the save and its notice must not repeat.
    const payload = Object.fromEntries(new FormData(form));
    payload.request_id = requestId;
    if (selected) payload.version = selected.version;
    const endpoint = selected ? `${base}/${encodeURIComponent(selected.id)}${action === 'cancel' ? '/cancel' : ''}` : base;
    setBusy(true);
    error.hidden = true;
    try {
      const response = await fetch(endpoint, {
        method: action === 'update' ? 'PATCH' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'No se pudo guardar la reunión. Intenta de nuevo.');
      if (result.delivery_status === 'unprepared') throw new Error(`Se guardó la reunión, pero no se pudo preparar el aviso. Pulsa «${submitLabel()}» para reintentar.`);
      window.location.hash = 'lead-meetings';
      window.location.reload();
    } catch (problem) {
      error.textContent = problem.message || 'Revisa tu conexión e intenta de nuevo. La misma acción no se duplicará.';
      error.hidden = false;
      setBusy(false);
    }
  });
})();
