(() => {
  const form = document.getElementById('contract-editor');
  const list = document.getElementById('clauses');
  const status = document.getElementById('contract-status');
  const selector = document.getElementById('contract-type');
  let appliedType = form.dataset.id;

  function appendClause(title = '', body = '') {
    const row = document.getElementById('empty-clause').content.firstElementChild.cloneNode(true);
    row.querySelector('[name="title"]').value = title;
    row.querySelector('[name="body"]').value = body;
    list.append(row);
    return row;
  }

  document.getElementById('add-clause').addEventListener('click', () => {
    appendClause().querySelector('input').focus();
  });
  list.addEventListener('click', event => {
    if (!event.target.matches('[data-remove]')) return;
    const row = event.target.closest('fieldset');
    if (row.querySelector('textarea').value && !confirm('¿Quitar esta cláusula?')) return;
    row.remove();
    document.getElementById('add-clause').focus();
  });
  if (selector) {
    document.getElementById('load-type').addEventListener('click', () => {
      if (!confirm('¿Reemplazar las cláusulas del editor por las de este tipo?')) return;
      const options = JSON.parse(document.getElementById('contract-options').textContent);
      appliedType = selector.value;
      list.replaceChildren();
      options[appliedType].clauses.forEach(([title, body]) => appendClause(title, body));
      status.textContent = 'Tipo aplicado. Guardá el contrato para confirmar.';
    });
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    if (submit.disabled) return;
    submit.disabled = true;
    status.classList.remove('contract-error');
    try {
      if (selector && selector.value !== appliedType) {
        throw new Error('Pulsá Aplicar tipo para cargar el tipo seleccionado.');
      }
      const payload = {
        template_id: appliedType,
        revision: Number(form.dataset.revision),
        clauses: Array.from(list.querySelectorAll('fieldset'), row => [
          row.querySelector('[name="title"]').value, row.querySelector('[name="body"]').value
        ])
      };
      if (!selector) {
        payload.name = document.getElementById('type-name').value;
        payload.make_default = document.getElementById('make-default').checked;
      }
      const result = await apiPost(form.dataset.url, payload);
      location.assign(result.url);
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('contract-error');
      submit.disabled = false;
    }
  });
})();
