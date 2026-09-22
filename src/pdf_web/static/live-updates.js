/* Own the queue event stream and its reconnect lifecycle. */
(function installPdfWebLiveUpdates(global) {
  'use strict';

  const INITIAL_RECONNECT_MS = 2000;
  const MAX_RECONNECT_MS = 60000;

  function connect(handlers) {
    let source = null;
    let reconnectDelay = INITIAL_RECONNECT_MS;
    let reconnectTimer = null;
    let closed = false;

    function deliver(event, handlerName) {
      if (typeof handlers[handlerName] !== 'function') return;
      try {
        handlers[handlerName](JSON.parse(event.data));
      } catch (_error) {
        // A malformed event is ignored; the next queue update or reconnect
        // snapshot will restore the current view.
      }
    }

    function open() {
      if (closed) return;
      source = new global.EventSource('/api/queue/events');
      source.onopen = () => { reconnectDelay = INITIAL_RECONNECT_MS; };
      source.addEventListener('queue', (event) => deliver(event, 'queue'));
      source.addEventListener('job-added', (event) => deliver(event, 'jobAdded'));
      source.addEventListener('job-updated', (event) => deliver(event, 'jobUpdated'));
      source.addEventListener('job-removed', (event) => deliver(event, 'jobRemoved'));
      source.addEventListener('queue-meta', (event) => deliver(event, 'queueMeta'));
      source.onerror = () => {
        if (closed || source === null) return;
        source.close();
        source = null;
        const delay = reconnectDelay;
        reconnectDelay = Math.min(delay * 2, MAX_RECONNECT_MS);
        reconnectTimer = global.setTimeout(open, delay);
      };
    }

    open();
    return {
      close() {
        closed = true;
        if (reconnectTimer !== null) global.clearTimeout(reconnectTimer);
        if (source !== null) source.close();
        reconnectTimer = null;
        source = null;
      },
    };
  }

  global.PdfWebLiveUpdates = Object.freeze({ connect });
})(window);
