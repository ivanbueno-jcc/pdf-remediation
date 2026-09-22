/* Own queue event-stream lifecycle and bounded reconnect behavior. */
(function installPdfWebLiveUpdates(global) {
  'use strict';
  const INITIAL_RECONNECT_MS = 2000, MAX_RECONNECT_MS = 60000;
  function connect(handlers) {
    let source = null, reconnectDelay = INITIAL_RECONNECT_MS, reconnectTimer = null, closed = false;
    function deliver(event, name) {
      if (typeof handlers[name] !== 'function') return;
      try { handlers[name](JSON.parse(event.data)); } catch (_error) { /* recover on next snapshot */ }
    }
    function open() {
      if (closed) return;
      source = new global.EventSource('/api/queue/events');
      source.onopen = () => { reconnectDelay = INITIAL_RECONNECT_MS; };
      ['queue', 'job-added', 'job-updated', 'job-removed', 'queue-meta'].forEach((eventName) => {
        const key = { 'job-added': 'jobAdded', 'job-updated': 'jobUpdated',
          'job-removed': 'jobRemoved', 'queue-meta': 'queueMeta' }[eventName] || eventName;
        source.addEventListener(eventName, (event) => deliver(event, key));
      });
      source.onerror = () => {
        if (closed || source === null) return;
        source.close(); source = null;
        const delay = reconnectDelay;
        reconnectDelay = Math.min(delay * 2, MAX_RECONNECT_MS);
        reconnectTimer = global.setTimeout(open, delay);
      };
    }
    open();
    return { close() {
      closed = true;
      if (reconnectTimer !== null) global.clearTimeout(reconnectTimer);
      if (source !== null) source.close();
      reconnectTimer = null; source = null;
    } };
  }
  global.PdfWebLiveUpdates = Object.freeze({ connect });
})(window);
