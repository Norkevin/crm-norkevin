// Keep date ranges consistent in the dashboard and calendar.
function formatFlowDate(value, includeTime) {
  if (!value) return '';
  const raw = String(value);
  const iso = raw.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return raw;
  const day = new Date(iso + 'T00:00:00Z');
  if (Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== iso) return raw;
  const label = new Intl.DateTimeFormat('es-GT', {day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(day);
  // Show the stored clock value; this is presentation, not a timezone conversion.
  return label + (includeTime && /^[T ]\d{2}:\d{2}/.test(raw.slice(10)) ? ' · ' + raw.slice(11,16) : '');
}

function syncDateRange(startId, endId, force) {
  const start = document.getElementById(startId);
  const end = document.getElementById(endId);
  end.min = start.value;
  if (start.value && (force || !end.value || end.value === end.dataset.syncedDate || end.value < start.value)) {
    end.value = start.value;
  }
  end.dataset.syncedDate = start.value;
}
