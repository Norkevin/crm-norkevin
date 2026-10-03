// The preview uses the same escaped HTML renderer as all delivery providers.
function installEmailPreview(subjectId, bodyId) {
  const subject = document.getElementById(subjectId);
  const body = document.getElementById(bodyId);
  if (!subject || !body) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'sn-btn';
  button.textContent = 'Vista previa del correo';
  const panel = document.createElement('div');
  panel.hidden = true;
  panel.style.margin = '16px 0';
  const warning = document.createElement('p');
  warning.setAttribute('role', 'status');
  const frame = document.createElement('iframe');
  frame.title = 'Así se verá el correo';
  frame.setAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
  frame.style.cssText = 'width:100%;height:420px;border:1px solid #dce3e7;border-radius:12px;background:white;';
  panel.append(warning, frame);
  body.parentElement.append(button, panel);
  button.addEventListener('click', async function () {
    button.disabled = true;
    try {
      const data = await apiPost('/api/email-preview', {subject: subject.value, body: body.value});
      frame.srcdoc = data.html;
      warning.textContent = data.unresolved.length
        ? 'Completa antes de enviar: ' + data.unresolved.join(', ')
        : 'Vista previa del mensaje. Revisa el destinatario y abre los enlaces antes de enviarlo.';
      warning.style.color = data.unresolved.length ? '#b4232a' : '#38596b';
      panel.hidden = false;
    } catch (error) {
      flashToast(error.message, 'error');
    } finally {
      button.disabled = false;
    }
  });
  [subject, body].forEach(input => input.addEventListener('input', () => { panel.hidden = true; }));
  // Changing a template invalidates its old preview as well.
  subject.closest('form').addEventListener('change', () => { panel.hidden = true; });
}
