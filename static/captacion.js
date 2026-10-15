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
  const googleEnabled = location.dataset?.googlePlaces === 'true';
  const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const words = text => normalize(text).split(' ').filter(word => word && !['de', 'del', 'la', 'las', 'el', 'los', 'y'].includes(word));
  const catalog = JSON.parse(document.getElementById('location-catalog')?.textContent || '[]').map(place => ({
    ...place, label: [place.name, place.detail].filter(Boolean).join(', '),
    names: [place.name, ...place.aliases].map(words), area: words(place.detail),
  }));
  function similarWord(query, word) {
    if (query === word) return 4;
    if (word.startsWith(query)) return 3;
    // ponytail: one-character mistakes cover venue names; no search dependency needed for 166 places.
    if (query.length < 4 || Math.abs(query.length - word.length) > 1) return 0;
    let row = Array.from({ length: word.length + 1 }, (_, index) => index);
    for (let i = 0; i < query.length; i++) {
      const next = [i + 1];
      for (let j = 0; j < word.length; j++) {
        next[j + 1] = Math.min(next[j] + 1, row[j + 1] + 1, row[j] + Number(query[i] !== word[j]));
      }
      row = next;
    }
    return row[word.length] <= 1 ? 1 : 0;
  }
  function localPlaces(query) {
    const terms = words(query);
    if (!terms.length) return [];
    return catalog.map(place => {
      const score = Math.max(...place.names.map(name => {
        const matches = terms.map(term => Math.max(0, ...name.map(word => similarWord(term, word)),
          ...place.area.map(word => similarWord(term, word) / 2)));
        return matches.every(Boolean) ? matches.reduce((sum, match) => sum + match, 0) / (name.length + 1) : 0;
      }));
      return { place, score };
    }).filter(result => result.score > 0).sort((a, b) => b.score - a.score || a.place.name.localeCompare(b.place.name, 'es'))
      .slice(0, 8).map(result => result.place);
  }
  function combinePlaces(local, remote) {
    const seen = new Set();
    return [...local, ...remote].filter(place => {
      const key = normalize(place.label);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 8);
  }
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
    const selected = suggestions[active]?.label;
    if (selected && !items.some(place => place.label === selected)) {
      items = [...items.slice(0, 7), suggestions[active]];
    }
    suggestions = items;
    active = items.findIndex(place => place.label === selected);
    location.removeAttribute('aria-activedescendant');
    results.replaceChildren();
    for (const [index, place] of items.entries()) {
      const item = document.createElement('li');
      item.id = `location-option-${index}`;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(index === active));
      if (index === active) location.setAttribute('aria-activedescendant', item.id);
      item.textContent = place.name;
      const detail = document.createElement('small');
      detail.textContent = place.detail;
      item.append(detail);
      if (place.provider === 'google') {
        const attribution = document.createElement('small');
        attribution.className = 'location-provider';
        attribution.setAttribute('translate', 'no');
        attribution.textContent = 'Google Maps';
        item.append(attribution);
      }
      item.addEventListener('mousedown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        choose(index);
      });
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
    if (query.length > 160 || /@|https?:\/\/|(?:\d[\s()-]*){7,}/i.test(query)) {
      status.textContent = 'Puedes escribir la ubicación completa y enviar tu solicitud.';
      return;
    }
    const currentRequest = requestId;
    const local = localPlaces(query);
    const queryKey = normalize(query);
    const terms = words(query);
    const cached = cache.get(queryKey) || [...cache.values()].flat().filter(place => {
      const candidateWords = words(place.label);
      return terms.length && terms.every(term => candidateWords.some(word => similarWord(term, word)));
    });
    const immediate = combinePlaces(local, cached);
    showResults(immediate);
    if (cache.has(queryKey) && !googleEnabled) return;
    status.textContent = immediate.length ? 'Selecciona un lugar o conserva lo que escribiste.' : 'Buscando lugares…';
    timer = setTimeout(async () => {
      const searchController = new AbortController();
      controller = searchController;
      const timeout = setTimeout(() => searchController.abort(), 8000);
      let photonItems = cached, googleItems = [];
      const publish = () => {
        if (currentRequest === requestId) showResults(combinePlaces(googleItems, combinePlaces(local, photonItems)));
      };
      const searches = [];
      if (!cache.has(queryKey)) searches.push(async () => {
        // ponytail: public Photon suits low-volume forms; use a hosted provider if traffic grows.
        const params = new URLSearchParams({ q: query, limit: '8', lat: '14.56', lon: '-90.73', countrycode: 'GT' });
        const response = await fetch(`https://photon.komoot.io/api/?${params}`, { signal: searchController.signal, referrerPolicy: 'no-referrer', credentials: 'omit' });
        if (!response.ok) throw new Error('Location search unavailable');
        const data = await response.json();
        if (currentRequest !== requestId) return;
        const seen = new Set();
        const items = (data.features || []).map(feature => {
          const p = feature.properties || {};
          const name = p.name || p.street || p.city || p.county;
          const detail = [...new Set([p.street, p.housenumber, p.city, p.state, p.country].filter(value => value && value !== name))].join(', ');
          return { name, detail, label: [name, detail].filter(Boolean).join(', ') };
        }).filter(place => {
          if (!place.name || seen.has(place.label)) return false;
          seen.add(place.label);
          return true;
        });
        photonItems = combinePlaces(items, cached);
        if (cache.size >= 30) cache.delete(cache.keys().next().value);
        cache.set(queryKey, photonItems);
        publish();
      });
      if (googleEnabled) searches.push(async () => {
        const response = await fetch('/api/captacion/places', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'omit',
          signal: searchController.signal,
          body: JSON.stringify({ query, tenant_slug: form.elements.tenant_slug.value }),
        });
        if (!response.ok) throw new Error('Google Places unavailable');
        const data = await response.json();
        if (!data.ok || !Array.isArray(data.items)) throw new Error('Google Places unavailable');
        googleItems = data.items;
        // Google predictions stay only in the current list, never in the Photon cache.
        publish();
      });
      try {
        const outcomes = await Promise.allSettled(searches.map(search => search()));
        if (currentRequest === requestId && outcomes.some(result => result.status === 'rejected')) {
          if (outcomes.every(result => result.status === 'rejected')) {
            status.textContent = immediate.length ? 'Puedes elegir un lugar sugerido o escribir otra ubicación.'
              : 'La búsqueda no está disponible. Puedes escribir el lugar y enviar tu solicitud.';
          } else if (!suggestions.length) {
            status.textContent = 'No pudimos completar la búsqueda. Puedes escribir el lugar y enviar tu solicitud.';
          }
        }
      } finally { clearTimeout(timeout); }
    }, 200);
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
    // Flatpickr's visible input is read-only, so native required validation is skipped.
    if (!dateInput.value) {
      error.textContent = 'Selecciona la fecha del evento para enviar tu solicitud.';
      error.hidden = false;
      (document.getElementById('fecha-visible') || dateInput).focus();
      return;
    }
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
