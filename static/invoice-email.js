// All invoice entry points share the same review and explicit approval flow.
(function () {
  const dialog = document.getElementById('invoice-email-dialog');
  const form = document.getElementById('invoice-email-form');
  const field = name => document.getElementById('invoice-email-' + name);
  const send = field('send');
  const cancel = field('cancel');
  const preview = field('preview');
  let paymentId, requestId, previewUrl, pendingId, revision = 0, sending = false;
  let reviewedSubject = '', reviewedBody = '';

  function payload() {
    return {to_email: field('to').value.trim(), subject: field('subject').value.trim(),
      body: field('body').value.trim().replaceAll(previewUrl, '[[INVOICE_LINK]]')};
  }

  function isReviewed() {
    const data = payload();
    return data.subject === reviewedSubject && data.body === reviewedBody;
  }

  async function refreshPreview() {
    const current = revision;
    const data = payload();
    if (!data.subject || !data.body) {
      send.disabled = true;
      field('status').textContent = 'Escribe el asunto y el mensaje antes de actualizar la vista previa.';
      return;
    }
    preview.disabled = send.disabled = true;
    field('frame').hidden = true;
    dialog.querySelector('details').open = false;
    field('status').textContent = 'Preparando la vista previa…';
    try {
      const result = await apiPost('/api/email-preview', {
        subject: data.subject,
        body: data.body.replaceAll('[[INVOICE_LINK]]', previewUrl)
      });
      if (!dialog.open || current !== revision) return;
      if (payload().subject !== data.subject || payload().body !== data.body) return;
      field('frame').srcdoc = result.html;
      field('frame').hidden = false;
      reviewedSubject = data.subject;
      reviewedBody = data.body;
      send.disabled = result.unresolved.length > 0;
      field('status').textContent = result.unresolved.length
        ? 'Completa antes de enviar: ' + result.unresolved.join(', ')
        : 'Revisa el destinatario y el mensaje. El correo saldrá al confirmar el envío.';
    } catch (error) {
      if (current === revision) field('status').textContent = error.message;
    } finally {
      if (current === revision) preview.disabled = false;
    }
  }

  window.sendInvoice = async function (id) {
    if (dialog.open) return;
    const current = ++revision;
    paymentId = id;
    requestId = crypto.randomUUID();
    pendingId = null;
    reviewedSubject = reviewedBody = '';
    form.reset();
    send.disabled = preview.disabled = true;
    cancel.disabled = false;
    cancel.textContent = 'Cerrar sin enviar';
    field('frame').hidden = true;
    field('review').hidden = true;
    field('from').textContent = '';
    field('status').textContent = 'Cargando el correo. Todavía no se ha enviado.';
    dialog.showModal();
    try {
      const response = await fetch('/api/payments/' + encodeURIComponent(id) + '/send-preview');
      const data = await response.json();
      if (!dialog.open || current !== revision) return;
      if (!response.ok || !data.ok) throw new Error(data.error || 'No se pudo preparar el correo. Cierra e inténtalo de nuevo.');
      field('to').value = data.to_email;
      field('subject').value = data.subject;
      previewUrl = data.preview_url;
      field('body').value = data.body.replaceAll('[[INVOICE_LINK]]', previewUrl);
      field('from').textContent = data.company + (data.from_email ? ' · Desde ' + data.from_email : ' · Gmail sin conectar');
      await refreshPreview();
    } catch (error) {
      if (current === revision) field('status').textContent = error.message;
    }
  };

  preview.addEventListener('click', refreshPreview);
  [field('subject'), field('body')].forEach(input => input.addEventListener('input', function () {
    field('frame').hidden = true;
    send.disabled = true;
    field('status').textContent = 'Actualiza la vista previa para revisar tus cambios antes de enviar.';
  }));
  cancel.addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (sending) event.preventDefault(); });
  dialog.addEventListener('close', () => { revision++; });

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (sending || send.disabled || !isReviewed() || !form.reportValidity()) return;
    sending = true;
    send.disabled = cancel.disabled = preview.disabled = true;
    const data = payload();
    [field('to'), field('subject'), field('body')].forEach(input => { input.readOnly = true; });
    field('status').textContent = 'Enviando el correo…';
    try {
      const queued = await apiPost('/api/payments/' + encodeURIComponent(paymentId) + '/send',
        {...data, send_request_id: requestId});
      pendingId = queued.mail_id;
      if (queued.delivery_status === 'pending') {
        await apiPost('/api/pending-emails/' + encodeURIComponent(pendingId) + '/send', {});
      } else if (queued.delivery_status !== 'sent') {
        throw new Error(queued.mail_warning || 'El correo no se pudo enviar. Revisa su estado en Correos.');
      }
      flashToast('Correo enviado a ' + data.to_email, 'success');
      dialog.close();
      location.reload();
    } catch (error) {
      field('status').textContent = error.message + ' Revisa el estado en Correos antes de volver a enviarlo.';
      field('review').hidden = false;
      cancel.textContent = 'Cerrar';
    } finally {
      sending = false;
      cancel.disabled = false;
      [field('to'), field('subject'), field('body')].forEach(input => { input.readOnly = false; });
    }
  });
})();
