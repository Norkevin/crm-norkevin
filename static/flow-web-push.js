/* Web Push belongs to this browser and the current owner account. No automatic permission prompts. */
(function () {
  'use strict';
  var state = {status: null, registration: null, subscription: null, registered: false, retryCheck: false, busy: false, error: '', pending: null};
  var ios = /iPad|iPhone|iPod/.test(navigator.userAgent || '') || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  function standalone() {
    return navigator.standalone === true || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  }
  function supported() {
    return window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }
  function element(id) { return document.getElementById(id); }
  function render() {
    var text = element('web-push-status');
    if (!text) return;
    var ready = state.status && state.status.available;
    var denied = 'Notification' in window && Notification.permission === 'denied';
    var waiting = state.registration && state.registration.waiting;
    var activate = element('web-push-enable'), disable = element('web-push-disable'), test = element('web-push-test');
    if (state.error) text.textContent = state.error;
    else if (ios && !standalone()) text.textContent = 'En Safari, toca Compartir → Añadir a pantalla de inicio. Abre Flowing desde ese icono para activar alertas.';
    else if (!supported()) text.textContent = 'Este navegador no permite alertas Web Push aquí. La bandeja de Flowing sigue disponible.';
    else if (!state.status) text.textContent = 'Comprobando las alertas de este navegador…';
    else if (denied) text.textContent = ios ? 'Alertas bloqueadas. Revisa Ajustes → Notificaciones → Flowing en el iPhone y vuelve a abrirla.' : 'Alertas bloqueadas. Permite las notificaciones en los ajustes de este sitio y vuelve a intentarlo.';
    else if (!ready) text.textContent = 'La bandeja funciona. El servidor todavía no está listo para alertas con Flowing cerrada.';
    else if (waiting) text.textContent = 'Hay una versión nueva de Flowing. Actualízala desde el aviso de actualización para activar alertas.';
    else if (state.registered && Notification.permission === 'granted') text.textContent = 'Alertas registradas para esta empresa en este navegador. Envía una prueba para comprobar la recepción.';
    else text.textContent = 'Puedes recibir alertas con Flowing cerrada. El permiso del navegador necesita una suscripción registrada en Flowing.';
    activate.textContent = state.retryCheck ? 'Reintentar comprobación' : 'Activar en este navegador';
    activate.hidden = !!(!state.retryCheck && state.registered && ready && Notification.permission === 'granted');
    activate.disabled = state.busy || (!state.retryCheck && (!ready || !state.registration || !supported() || (ios && !standalone()) || denied || !!waiting));
    disable.hidden = !state.subscription;
    disable.disabled = state.busy;
    test.hidden = !state.registered;
    test.disabled = state.busy || !ready || !('Notification' in window) || Notification.permission !== 'granted';
    var feedback = element('web-push-feedback');
    if (feedback) feedback.textContent = state.busy ? 'Guardando…' : '';
  }
  async function endpointID(endpoint) {
    var digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint));
    return Array.from(new Uint8Array(digest), function (byte) { return byte.toString(16).padStart(2, '0'); }).join('');
  }
  async function request(path, value) {
    var options = {credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: {'Accept': 'application/json'}};
    if (value !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.headers['X-CSRF-Token'] = state.status.csrf;
      options.body = JSON.stringify(value);
    }
    var response = await fetch('/api/web-push/' + path, options);
    var data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'No se pudo guardar la configuración. Vuelve a abrir la campana e inténtalo otra vez.');
    return data;
  }
  async function refresh() {
    if (state.pending) return state.pending;
    state.pending = (async function () {
      state.error = ''; state.registered = false; state.retryCheck = false;
      try {
        state.status = await request('status');
        if (supported() && (!ios || standalone())) {
          state.registration = await navigator.serviceWorker.getRegistration('/');
          if (!state.registration) state.registration = await navigator.serviceWorker.register('/service-worker.js');
          state.registration = await navigator.serviceWorker.ready;
          state.subscription = await state.registration.pushManager.getSubscription();
          if (state.subscription) {
            var id = await endpointID(state.subscription.endpoint);
            if ((state.status.subscription_ids || []).indexOf(id) !== -1) {
              // Restore logout's browser binding after a new web session, without reactivating subscriptions.
              var result = await request('bind', {endpoint: state.subscription.endpoint});
              if (result.ok !== true || result.id !== id) throw new Error('El servidor no confirmó este navegador.');
              state.registered = true;
            }
          }
        }
      } catch (error) {
        state.retryCheck = true;
        state.error = 'No se pudo comprobar la suscripción. Reintenta la comprobación; la bandeja sigue disponible.';
      }
      render();
      state.pending = null;
    })();
    return state.pending;
  }
  function userGesture(event) {
    return event && event.isTrusted === true && (!navigator.userActivation || navigator.userActivation.isActive);
  }
  function vapidKey(value) {
    var base64 = value.replace(/-/g, '+').replace(/_/g, '/');
    var decoded = atob(base64 + '='.repeat((4 - base64.length % 4) % 4));
    return Uint8Array.from(decoded, function (char) { return char.charCodeAt(0); });
  }
  async function activate(event) {
    if (!userGesture(event) || state.busy) return;
    if (state.retryCheck) { await refresh(); return; }
    if (!supported() || !state.registration || (ios && !standalone()) || !state.status || !state.status.available || state.registration.waiting) return;
    if (Notification.permission === 'denied') { render(); return; }
    state.busy = true; state.error = ''; render();
    try {
      // Call synchronously from the click: awaiting network first loses Safari's user gesture.
      var permission = Notification.permission === 'default' ? Notification.requestPermission() : Promise.resolve(Notification.permission);
      if (await permission !== 'granted') { render(); return; }
      var subscription = state.subscription;
      if (subscription && !state.registered) {
        if (!await subscription.unsubscribe()) throw new Error('No se pudo renovar la suscripción de este navegador. Reintenta.');
        subscription = null; state.subscription = null;
      }
      if (!subscription) subscription = await state.registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: vapidKey(state.status.vapid_public_key)});
      state.subscription = subscription;
      var result = await request('subscribe', {subscription: subscription.toJSON(), hide_details: true});
      if (!result.ok || result.id !== await endpointID(subscription.endpoint)) throw new Error('Flowing no confirmó esta suscripción. Vuelve a abrir la campana para comprobarla.');
      state.registered = true;
    } catch (error) { state.error = error.message || 'No se pudieron activar las alertas.'; }
    finally { state.busy = false; render(); }
  }
  async function deactivate(event) {
    if (!userGesture(event) || state.busy || !state.subscription) return;
    state.busy = true; state.error = ''; render();
    try {
      await request('unsubscribe', {endpoint: state.subscription.endpoint});
      state.registered = false;
      if (!await state.subscription.unsubscribe()) throw new Error('Flowing ya dejó de enviar aquí, pero el navegador no terminó de cancelar su suscripción. Reintenta.');
      state.subscription = null;
      state.error = 'Alertas desactivadas en este navegador. Los otros dispositivos conservan su configuración.';
    } catch (error) { state.error = error.message || 'No se pudo desactivar este navegador. Reintenta.'; }
    finally { state.busy = false; render(); }
  }
  async function testAlert(event) {
    if (!userGesture(event) || state.busy || !state.registered || !state.status.available || Notification.permission !== 'granted') return;
    state.busy = true; state.error = ''; render();
    try {
      await request('test', {endpoint: state.subscription.endpoint});
      state.error = 'Prueba en cola para este navegador. Cierra Flowing y comprueba el aviso; esto todavía no confirma su recepción.';
    } catch (error) { state.error = error.message || 'No se pudo enviar la prueba a este navegador.'; }
    finally { state.busy = false; render(); }
  }
  window.FlowWebPush = {refresh: refresh, activate: activate, deactivate: deactivate, test: testAlert,
    isRegistered: function () { return !!(state.registered && state.status && state.status.available && 'Notification' in window && Notification.permission === 'granted'); }};
  document.addEventListener('DOMContentLoaded', function () {
    if (!element('web-push-status')) return;
    element('web-push-enable').addEventListener('click', activate);
    element('web-push-disable').addEventListener('click', deactivate);
    element('web-push-test').addEventListener('click', testAlert);
    refresh();
  });
})();
