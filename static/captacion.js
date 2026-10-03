/* Shared public contact form for all brands. */
(() => {
  'use strict';
  const form = document.getElementById('form-captacion');
  const dateInput = document.getElementById('fecha_tentativa');
  const clearDate = document.getElementById('clear-date');
  if (window.flatpickr) {
    const picker = flatpickr(dateInput, {
      locale: { ...flatpickr.l10ns.es, monthAriaLabel: 'Mes', yearAriaLabel: 'Año' }, dateFormat: 'Y-m-d', altInput: true, altFormat: 'j M Y',
      ariaDateFormat: 'j \\d\\e F \\d\\e Y', disableMobile: true,
      minDate: 'today',
      onReady: (_, __, instance) => {
        instance.altInput.id = 'fecha-visible';
        instance.altInput.placeholder = 'Selecciona una fecha';
        instance.altInput.setAttribute('aria-describedby', 'date-hint');
        document.querySelector('label[for="fecha_tentativa"]').htmlFor = 'fecha-visible';
        document.querySelector('.calendar-icon').hidden = false;
      },
      onChange: dates => { clearDate.hidden = !dates.length; },
    });
    clearDate.addEventListener('click', () => {
      picker.clear();
      picker.altInput.focus();
    });
  }

  const location = document.getElementById('locacion');
  const results = document.getElementById('location-results');
  const status = document.getElementById('location-status');
  let timer, controller, active = -1, suggestions = [], requestId = 0;
  const cache = new Map();
  function closeResults() {
    results.hidden = true;
    location.setAttribute('aria-expanded', 'false');
    location.removeAttribute('aria-activedescendant');
    active = -1;
  }
  function cancelSearch() {
    clearTimeout(timer);
    if (controller) controller.abort();
    requestId++;
  }
  function choose(index) {
    location.value = suggestions[index].label;
    cancelSearch();
    closeResults();
    status.textContent = 'Ubicación seleccionada. Puedes editarla si lo necesitas.';
  }
  function showResults(items) {
    suggestions = items;
    active = -1;
    results.replaceChildren();
    for (const [index, place] of items.entries()) {
      const item = document.createElement('li');
      item.id = `location-option-${index}`;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', 'false');
      item.textContent = place.name;
      const detail = document.createElement('small');
      detail.textContent = place.detail;
      item.append(detail);
      item.addEventListener('pointerdown', event => event.preventDefault());
      item.addEventListener('click', () => choose(index));
      results.append(item);
    }
    results.hidden = !items.length;
    location.setAttribute('aria-expanded', String(!!items.length));
    status.textContent = items.length ? 'Selecciona un lugar o conserva lo que escribiste.' : 'No encontramos ese lugar. Puedes escribir la dirección o una referencia.';
  }
  location.addEventListener('input', () => {
    cancelSearch();
    closeResults();
    const query = location.value.trim();
    if (query.length < 3) {
      status.textContent = 'Escribe al menos 3 letras o deja tu ubicación por definir.';
      return;
    }
    const currentRequest = requestId;
    timer = setTimeout(async () => {
      if (cache.has(query)) { showResults(cache.get(query)); return; }
      const searchController = new AbortController();
      controller = searchController;
      const timeout = setTimeout(() => searchController.abort(), 8000);
      status.textContent = 'Buscando lugares…';
      try {
        // ponytail: public Photon suits low-volume forms; use a hosted provider if traffic grows.
        const params = new URLSearchParams({ q: query, limit: '5', lat: '14.56', lon: '-90.73' });
        const response = await fetch(`https://photon.komoot.io/api/?${params}`, { signal: searchController.signal, referrerPolicy: 'no-referrer', credentials: 'omit' });
        if (!response.ok) throw new Error('Location search unavailable');
        const data = await response.json();
        if (currentRequest !== requestId) return;
        const seen = new Set();
        const items = (data.features || []).map(feature => {
          const p = feature.properties;
          const name = p.name || p.street || p.city || p.county;
          const detail = [...new Set([p.street, p.housenumber, p.city, p.state, p.country].filter(value => value && value !== name))].join(', ');
          return { name, detail, label: [name, detail].filter(Boolean).join(', ') };
        }).filter(place => {
          if (!place.name || seen.has(place.label)) return false;
          seen.add(place.label);
          return true;
        });
        if (cache.size >= 30) cache.delete(cache.keys().next().value);
        cache.set(query, items);
        showResults(items);
      } catch (_) {
        if (currentRequest === requestId) status.textContent = 'La búsqueda no está disponible. Puedes escribir el lugar y enviar tu solicitud.';
      } finally { clearTimeout(timeout); }
    }, 450);
  });
  location.addEventListener('keydown', event => {
    if (event.key === 'Escape') { cancelSearch(); closeResults(); return; }
    if (results.hidden) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      active = active < 0 ? (direction === 1 ? 0 : suggestions.length - 1) : (active + direction + suggestions.length) % suggestions.length;
      [...results.children].forEach((item, index) => item.setAttribute('aria-selected', String(index === active)));
      location.setAttribute('aria-activedescendant', results.children[active].id);
      results.children[active].scrollIntoView({ block: 'nearest' });
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      choose(active);
    }
  });
  location.addEventListener('blur', () => {
    cancelSearch();
    closeResults();
    if (status.textContent === 'Buscando lugares…') status.textContent = 'Puedes escribir el lugar o volver a buscar.';
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    const label = document.getElementById('submit-label');
    const error = document.getElementById('form-error');
    error.hidden = true;
    button.disabled = true;
    label.textContent = 'Enviando…';
    try {
      const response = await fetch('/api/captacion', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'No pudimos enviar tu solicitud. Inténtalo de nuevo.');
      cancelSearch();
      closeResults();
      form.hidden = true;
      const success = document.getElementById('success-msg');
      success.hidden = false;
      success.focus();
      if (typeof window.trackSavedLead === 'function') window.trackSavedLead(result.lead_id);
    } catch (failure) {
      error.textContent = failure instanceof TypeError ? 'No pudimos conectar. Tus datos siguen aquí; revisa tu conexión e inténtalo de nuevo.' : failure.message;
      error.hidden = false;
      button.disabled = false;
      label.textContent = 'Enviar solicitud';
    }
  });
})();
