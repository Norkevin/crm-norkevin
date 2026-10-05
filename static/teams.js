(() => {
  const feedback = document.querySelector('#ft-feedback');
  const showError = message => {
    feedback.textContent = message;
    feedback.hidden = false;
    feedback.scrollIntoView({block: 'center', behavior: 'smooth'});
  };
  async function send(data, button, source) {
    if (button.disabled) return;
    const file = source.elements?.file?.files[0];
    const fileLimit = source.dataset.command === 'directory_import' ? 20 : 10;
    if (file && file.size > fileLimit * 1024 * 1024) { showError(`El archivo supera ${fileLimit} MB.`); return; }
    const key = source.dataset.commandKey || crypto.randomUUID();
    const body = JSON.stringify({...data, key});
    // Retrying an uncertain result keeps the same key and exact content.
    if (source.dataset.commandBody && source.dataset.commandBody !== body) {
      showError('Hay un registro pendiente de comprobar. Recarga para revisar el historial antes de cambiarlo.');
      return;
    }
    source.dataset.commandKey = key;
    source.dataset.commandBody = body;
    button.disabled = true;
    try {
      let endpoint = source.dataset.endpoint || window.flowTeamsEndpoint || '/api/teams/command';
      let headers = {'Content-Type': 'application/json', 'X-Teams-CSRF': window.flowTeamsCSRF};
      let payload = body;
      if (source.dataset.upload) {
        endpoint = source.dataset.endpoint || '/api/teams/documents/upload';
        payload = new FormData(source);
        payload.set('key', key);
        if (data.action === 'payment') payload.set('allocations', JSON.stringify(data.allocations));
        delete headers['Content-Type'];
      }
      const response = await fetch(endpoint, {method: 'POST', headers, body: payload});
      const result = await response.json();
      if (!response.ok) {
        delete source.dataset.commandKey;
        delete source.dataset.commandBody;
        throw new Error(result.error || 'No se pudo guardar. Recarga y comprueba tu sesión.');
      }
      if (result.warnings?.length) sessionStorage.setItem('flow-teams-message', result.warnings.join(' '));
      location.reload();
    } catch (error) {
      showError(source.dataset.commandKey ? 'No se confirmó el resultado. Reintenta el mismo formulario o recarga para revisar el historial.' : error.message);
      button.disabled = false;
    }
  }
  // Native pickers keep ISO values; the visible caption uses the stored day only.
  const formatDate = value => {
    if (!value) return 'Selecciona una fecha';
    const [year, month, day] = value.slice(0, 10).split('-').map(Number);
    const date = new Intl.DateTimeFormat('es-GT', {day:'numeric', month:'long', year:'numeric', timeZone:'UTC'})
      .format(new Date(Date.UTC(year, month - 1, day)));
    return date + (value.length > 10 ? ' · ' + value.slice(11, 16) : '');
  };
  const dateControls = scope => scope.querySelectorAll('input[type="date"],input[type="datetime-local"]').forEach(input => {
    const existing = input.closest('.ft-date-control');
    const control = existing || document.createElement('span');
    control.className = 'ft-date-control';
    const caption = existing?.querySelector('.ft-date-caption') || document.createElement('span');
    caption.className = 'ft-date-caption';
    caption.setAttribute('aria-hidden', 'true');
    if (!existing) { input.before(control); control.append(caption, input); }
    const update = () => { caption.textContent = formatDate(input.value); };
    input.addEventListener('input', update); input.addEventListener('change', update);
    input.addEventListener('click', () => { try { input.showPicker?.(); } catch (_) { /* Native keyboard fallback. */ } });
    update();
  });
  dateControls(document);
  document.querySelectorAll('form[data-command="schedule"]').forEach(form => {
    const rows = form.querySelector('[data-schedule-rows]');
    const prototype = rows.firstElementChild.cloneNode(true);
    form.querySelector('[data-add-quota]').addEventListener('click', () => {
      const row = prototype.cloneNode(true);
      row.querySelectorAll('input').forEach(input => { input.value = ''; });
      rows.append(row); dateControls(row); row.querySelector('input').focus();
    });
    rows.addEventListener('click', event => {
      if (event.target.closest('[data-remove-quota]') && rows.children.length > 1)
        event.target.closest('.ft-schedule-row').remove();
    });
  });
  document.querySelectorAll('form[data-command]').forEach(form => {
    form.addEventListener('submit', event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(form));
      data.action = form.dataset.command;
      if (data.action === 'schedule') data.plan = [...form.querySelectorAll('.ft-schedule-row')]
        .map(row => `${row.querySelector('[data-quota-date]').value} ${row.querySelector('[data-quota-amount]').value}`).join('\n');
      if ('version' in data) data.version = Number(data.version);
      if ('terms_version' in data) data.terms_version = Number(data.terms_version);
      if ('active' in data) data.active = data.active === 'true';
      if (data.action === 'operation') data.reviewed = form.elements.reviewed.checked;
      if (data.action === 'payment') {
        data.allocations = data.cost_id ? [{cost_id: data.cost_id, amount: data.amount}] : [...form.querySelectorAll('[data-cost-id]')]
          .filter(input => !input.closest('[data-beneficiary]').hidden && Number(input.value) > 0)
          .map(input => ({cost_id: input.dataset.costId, amount: input.value}));
      }
      if (data.action === 'settlement') data.allocations = [...form.querySelectorAll('[data-cost-id]')]
        .filter(input => Number(input.value) > 0).map(input => ({cost_id: input.dataset.costId, amount: input.value}));
      if (data.action === 'cost_shared') data.distribution = [...form.querySelectorAll('[data-job-id]')]
        .filter(input => Number(input.value) > 0).map(input => ({job_id: input.dataset.jobId, amount: input.value}));
      if (form.dataset.upload) {
        data.audience_ids = new FormData(form).getAll('audience_ids');
        const file = form.elements.file?.files[0];
        data.file = file ? {name: file.name, size: file.size, modified: file.lastModified} : null;
      }
      send(data, event.submitter || form.querySelector('button'), form);
    });
  });
  document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => {
    const data = {action: button.dataset.action, id: button.dataset.id, version: Number(button.dataset.version)};
    if (button.dataset.status) data.status = button.dataset.status;
    send(data, button, button);
  }));
  document.querySelectorAll('select[name="member_id"]').forEach(select => select.addEventListener('change', () => {
    const form = select.form;
    if (form.dataset.command !== 'assignment') return;
    const option = select.selectedOptions[0];
    form.elements.amount.value = option.dataset.rate || '';
  }));
  const beneficiary = document.querySelector('#ft-beneficiary');
  if (beneficiary) beneficiary.addEventListener('change', () => {
    document.querySelectorAll('[data-beneficiary]').forEach(row => {
      row.hidden = row.dataset.beneficiary !== beneficiary.value;
      row.querySelector('input').value = '0';
    });
    beneficiary.form.elements.amount.value = '0.00';
  });
  document.querySelectorAll('[data-pay-beneficiary]').forEach(button => button.addEventListener('click', () => {
    if (beneficiary.form.dataset.commandKey) {
      showError('Hay un abono pendiente de confirmar. Recarga y revisa el historial antes de registrar otro.'); return;
    }
    beneficiary.form.reset();
    beneficiary.form.querySelectorAll('input[type="date"]').forEach(input => input.dispatchEvent(new Event('input')));
    beneficiary.value = button.dataset.payBeneficiary;
    beneficiary.dispatchEvent(new Event('change'));
    const editor = document.querySelector('#ft-payment-editor');
    editor.hidden = false;
    const full = button.hasAttribute('data-pay-full');
    editor.querySelector('[data-payment-title]').textContent = `${full ? 'Pago completo' : 'Abono'} a ${button.dataset.payName}`;
    if (full) {
      editor.querySelectorAll('[data-beneficiary]:not([hidden]) input').forEach(input => { input.value = input.max; });
      beneficiary.form.dispatchEvent(new Event('input'));
    }
    editor.scrollIntoView({block:'start', behavior:'smooth'});
    editor.querySelector('[data-beneficiary]:not([hidden]) input').focus({preventScroll:true});
  }));
  document.querySelector('[data-payment-cancel]')?.addEventListener('click', () => {
    document.querySelector('#ft-payment-editor').hidden = true;
    document.querySelector('[data-pay-beneficiary]')?.focus();
  });
  if (beneficiary) beneficiary.form.addEventListener('input', () => {
    const total = [...beneficiary.form.querySelectorAll('[data-cost-id]')]
      .filter(input => !input.closest('[data-beneficiary]').hidden)
      .reduce((sum, input) => sum + Math.round(Number(input.value || 0) * 100), 0);
    beneficiary.form.elements.amount.value = (total / 100).toFixed(2);
  });
  document.querySelectorAll('[data-job-tab]').forEach(button => {
    const activate = () => {
      sessionStorage.setItem(`flow-teams-tab:${location.pathname}`, button.dataset.jobTab);
      document.querySelectorAll('[data-job-tab]').forEach(tab => {
        const selected = tab === button;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        document.querySelector(`#ft-job-${tab.dataset.jobTab}`).hidden = !selected;
      });
    };
    button.addEventListener('click', activate);
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...document.querySelectorAll('[data-job-tab]')];
      let index = tabs.indexOf(button);
      index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 :
        (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[index].click(); tabs[index].focus();
    });
  });
  const rememberedTab = sessionStorage.getItem(`flow-teams-tab:${location.pathname}`);
  const restoreTab = [...document.querySelectorAll('[data-job-tab]')]
    .find(button => button.dataset.jobTab === rememberedTab);
  if (restoreTab) restoreTab.click();
  document.querySelectorAll('[data-access]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const response = await fetch('/api/teams/access', {method: 'POST', headers: {
        'Content-Type':'application/json', 'X-Teams-CSRF':window.flowTeamsCSRF}, body:JSON.stringify({member_id:button.dataset.access})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'No se pudo crear el código.');
      const output = document.querySelector(`[data-access-output="${button.dataset.access}"]`);
      output.textContent = `${data.message}\n${data.code}\nAcceso: http://127.0.0.1:5052/teams-portal/login`;
      output.hidden = false;
    } catch (error) { showError(error.message); }
    finally { button.disabled = false; }
  }));
  document.querySelectorAll('[data-search]').forEach(input => input.addEventListener('input', () => {
    if (input.dataset.search === 'calendar') {
      document.querySelectorAll('[data-calendar-search]').forEach(card => {
        card.hidden = !card.dataset.calendarSearch.toLocaleLowerCase().includes(input.value.toLocaleLowerCase());
      });
      document.querySelectorAll('[data-calendar-day]').forEach(day => {
        day.hidden = !day.querySelector('[data-calendar-search]:not([hidden])');
      });
    }
    if (input.dataset.search === 'members') document.querySelectorAll('[data-member-search]').forEach(card => {
      card.hidden = !card.dataset.memberSearch.toLocaleLowerCase().includes(input.value.toLocaleLowerCase());
    });
    document.querySelectorAll(`[data-search-table="${input.dataset.search}"] tbody tr`).forEach(row => {
      row.hidden = !row.textContent.toLocaleLowerCase().includes(input.value.toLocaleLowerCase());
    });
  }));
  const savedMessage = sessionStorage.getItem('flow-teams-message');
  if (savedMessage) {
    sessionStorage.removeItem('flow-teams-message');
    showError(savedMessage);
  }
})();
