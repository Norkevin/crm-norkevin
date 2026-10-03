// Both public forms report only a saved lead, to the brand chosen by the server.
(() => {
  const config = document.getElementById('lead-ads-config');
  if (!config) return;
  const saved = new Set();
  window.trackSavedLead = leadId => {
    if (typeof leadId !== 'string' || !leadId || saved.has(leadId)) return;
    saved.add(leadId);
    if (typeof window.gtag === 'function') {
      try {
        window.gtag('event', 'conversion', {
          send_to: config.dataset.googleConversion, transaction_id: leadId,
        });
      } catch (_) { /* Tracking failures must not cause duplicate inquiries. */ }
    }
    if (typeof window.fbq === 'function') {
      try {
        window.fbq('trackSingle', config.dataset.metaPixel, 'Lead', {}, { eventID: leadId });
      } catch (_) { /* A blocked pixel must not interrupt the saved form. */ }
    }
  };
})();
