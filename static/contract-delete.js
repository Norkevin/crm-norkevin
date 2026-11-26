document.addEventListener('click', async function (event) {
  const button = event.target.closest('button[data-contract-delete]');
  if (!button || button.disabled) return;
  const signed = button.dataset.signed === 'true';
  const message = '¿Borrar este contrato? Su enlace dejará de funcionar y podrás crear y enviar uno nuevo.';
  if (!window.confirm(signed ? message + ' Las firmas quedarán archivadas con esta versión.' : message)) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/contracts/' + encodeURIComponent(button.dataset.contractDelete), {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirm_signed: signed})
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || 'No se pudo borrar el contrato.');
    location.assign(button.dataset.returnUrl);
  } catch (error) {
    button.disabled = false;
    window.alert(error.message);
  }
});
