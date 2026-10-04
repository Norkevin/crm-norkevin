// Keep date ranges consistent in the dashboard and calendar.
function syncDateRange(startId, endId, force) {
  const start = document.getElementById(startId);
  const end = document.getElementById(endId);
  end.min = start.value;
  if (start.value && (force || !end.value || end.value === end.dataset.syncedDate || end.value < start.value)) {
    end.value = start.value;
  }
  end.dataset.syncedDate = start.value;
}
