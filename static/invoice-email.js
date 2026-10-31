// Invoices, quotes, and contracts share the same review and explicit approval flow.
(function () {
  const dialog = document.getElementById('invoice-email-dialog');
  const form = document.getElementById('invoice-email-form');
  const field = name => document.getElementById('invoice-email-' + name);
  const send = field('send');
  const cancel = field('cancel');
  const preview = field('preview');
  const kinds = {
    invoice: {path: '/api/payments/', marker: '[[INVOICE_LINK]]'},
    quote: {path: '/api/quotes/', marker: '[[QUOTE_LINK]]'},
    contract: {path: '/api/contracts/', marker: '[[CONTRACT_LINK]]'}
  };
  // The quote editor is standalone and does not load the CRM's request helper.
  const post = typeof apiPost === 'function' ? apiPost : async function (url, payload) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'No se pudo completar la acción');
    return data;
  };
  let documentId, kind, config, sendOptions, requestId, previewUrl, pendingId, revision = 0, sending = false;
  let useTemplate = false, previewRevision = 0;
  let reviewedSubject = '', reviewedBody = '';

  function payload() {
    return {to_email: field('to').value.trim(), subject: field('subject').value.trim(),
      body: field('body').value.trim().replaceAll(previewUrl, config.marker),
      purpose: sendOptions.purpose || 'invoice',
      template_id: field('template').value || null, workflow_step_id: sendOptions.workflow_step_id || null};
  }

  function isReviewed() {
    const data = payload();
    return data.subject === reviewedSubject && data.body === reviewedBody;
  }

  async function refreshPreview() {
    const current = revision;
    const currentPreview = ++previewRevision;
    const data = payload();
    if ((!data.subject || !data.body) && !useTemplate) {
      send.disabled = true;
      field('status').textContent = 'Escribe el asunto y el mensaje antes de actualizar la vista previa.';
      return;
    }
    preview.disabled = send.disabled = true;
    // Keep the frame laid out so reloading srcdoc cannot leave its document at zero size.
    field('frame').style.visibility = 'hidden';
    dialog.querySelector('details').open = false;
    field('status').textContent = 'Preparando la vista previa…';
    try {
      const result = kind === 'invoice'
        ? await post('/api/email-preview', {subject: data.subject, body: data.body.replaceAll(config.marker, previewUrl)})
        : await post(config.path + encodeURIComponent(documentId) + '/send-preview', {...data, use_template: useTemplate});
      if (!dialog.open || current !== revision || currentPreview !== previewRevision) return;
      if (payload().subject !== data.subject || payload().body !== data.body) return;
      if (kind !== 'invoice') {
        field('subject').value = result.subject;
        field('body').value = result.body.replaceAll(config.marker, previewUrl);
        useTemplate = false;
      }
      field('frame').style.visibility = 'visible';
      field('frame').srcdoc = result.html;
      reviewedSubject = payload().subject;
      reviewedBody = payload().body;
      send.disabled = result.unresolved.length > 0;
      field('status').textContent = result.unresolved.length
        ? 'Completa antes de enviar: ' + result.unresolved.join(', ')
        : 'Revisa el destinatario y el mensaje. El correo saldrá al confirmar el envío.';
    } catch (error) {
      if (current === revision && currentPreview === previewRevision) field('status').textContent = error.message;
    } finally {
      if (current === revision && currentPreview === previewRevision) preview.disabled = false;
    }
  }

  window.sendInvoice = id => window.reviewDocumentEmail('invoice', id);
  window.sendPaymentReceipt = id => window.reviewDocumentEmail('invoice', id, {purpose: 'receipt'});

  window.reviewDocumentEmail = async function (documentKind, id, options = {}) {
    if (dialog.open) return;
    const current = ++revision;
    documentId = id;
    kind = documentKind;
    config = kinds[kind];
    sendOptions = options;
    field('title').textContent = options.purpose === 'receipt' ? 'Enviar recibo de pago' : 'Revisar antes de enviar';
    useTemplate = false;
    requestId = crypto.randomUUID();
    pendingId = null;
    reviewedSubject = reviewedBody = '';
    form.reset();
    send.disabled = preview.disabled = true;
    cancel.disabled = false;
    cancel.textContent = 'Cerrar sin enviar';
    field('frame').style.visibility = 'hidden';
    field('review').hidden = true;
    field('from').textContent = '';
    field('status').textContent = 'Cargando el correo. Todavía no se ha enviado.';
    dialog.showModal();
    try {
      const params = new URLSearchParams();
      if (options.template_id) params.set('template_id', options.template_id);
      if (options.purpose) params.set('purpose', options.purpose);
      const query = params.size ? '?' + params.toString() : '';
      const response = await fetch(config.path + encodeURIComponent(id) + '/send-preview' + query);
      const data = await response.json();
      if (!dialog.open || current !== revision) return;
      if (!response.ok || !data.ok) throw new Error(data.error || 'No se pudo preparar el correo. Cierra e inténtalo de nuevo.');
      field('template-group').hidden = !(data.templates || []).length;
      field('template').replaceChildren(new Option('Mensaje predeterminado', ''),
        ...(data.templates || []).map(template => new Option(template.name, template.id)));
      field('template').value = options.template_id || '';
      field('to').value = data.to_email;
      field('subject').value = data.subject;
      previewUrl = data.preview_url;
      field('body').value = data.body.replaceAll(config.marker, previewUrl);
      field('from').textContent = data.company + (data.from_email ? ' · Desde ' + data.from_email : ' · Gmail sin conectar');
      await refreshPreview();
    } catch (error) {
      if (current === revision) field('status').textContent = error.message;
    }
  };

  preview.addEventListener('click', refreshPreview);
  field('template').addEventListener('change', function () {
    useTemplate = true;
    return refreshPreview();
  });
  [field('subject'), field('body')].forEach(input => input.addEventListener('input', function () {
    previewRevision++;
    useTemplate = false;
    preview.disabled = false;
    field('frame').style.visibility = 'hidden';
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
    field('template').disabled = true;
    const data = payload();
    [field('to'), field('subject'), field('body')].forEach(input => { input.readOnly = true; });
    field('status').textContent = 'Enviando el correo…';
    try {
      if (sendOptions.beforeSend && await sendOptions.beforeSend() === false) {
        field('status').textContent = 'No se pudo guardar la preparación. Corrígela antes de enviar.';
        send.disabled = preview.disabled = false;
        return;
      }
      const queued = await post(config.path + encodeURIComponent(documentId) + '/send',
        {...data, send_request_id: requestId});
      pendingId = queued.mail_id;
      if (queued.delivery_status === 'pending') {
        await post('/api/pending-emails/' + encodeURIComponent(pendingId) + '/send', {});
      } else if (queued.delivery_status !== 'sent') {
        throw new Error(queued.mail_warning || 'El correo no se pudo enviar. Revisa su estado en Correos.');
      }
      if (typeof flashToast === 'function') flashToast('Correo enviado a ' + data.to_email, 'success');
      dialog.close();
      if (sendOptions.onSent) sendOptions.onSent();
      else location.reload();
    } catch (error) {
      field('status').textContent = error.message + ' Revisa el estado en Correos antes de volver a enviarlo.';
      field('review').hidden = false;
      cancel.textContent = 'Cerrar';
    } finally {
      sending = false;
      field('template').disabled = false;
      cancel.disabled = false;
      [field('to'), field('subject'), field('body')].forEach(input => { input.readOnly = false; });
    }
  });
})();
