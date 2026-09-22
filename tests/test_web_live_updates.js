'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadLiveUpdates() {
  const streams = [];
  const timers = [];
  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = new Map();
      this.closed = false;
      streams.push(this);
    }

    addEventListener(name, callback) {
      this.listeners.set(name, callback);
    }

    close() { this.closed = true; }
    emit(name, data) { this.listeners.get(name)({ data }); }
  }

  const window = {
    EventSource: FakeEventSource,
    setTimeout(callback, delay) {
      const timer = { callback, delay, cleared: false };
      timers.push(timer);
      return timers.length;
    },
    clearTimeout(id) { timers[id - 1].cleared = true; },
  };
  const context = { window };
  vm.createContext(context);
  const modulePath = path.join(
    __dirname, '..', 'src', 'pdf_web', 'static', 'sse.js'
  );
  vm.runInContext(fs.readFileSync(modulePath, 'utf8'), context, {
    filename: modulePath,
  });
  return { liveUpdates: window.PdfWebLiveUpdates, streams, timers };
}

test('queue stream dispatches decoded payloads by event type', () => {
  const { liveUpdates, streams } = loadLiveUpdates();
  const received = [];
  liveUpdates.connect({ queue: (payload) => received.push(payload) });

  assert.equal(streams.length, 1);
  assert.equal(streams[0].url, '/api/queue/events');
  streams[0].emit('queue', '{"jobs":[]}');
  assert.deepEqual(JSON.parse(JSON.stringify(received)), [{ jobs: [] }]);
});

test('queue stream reconnects with increasing delay and resets after opening', () => {
  const { liveUpdates, streams, timers } = loadLiveUpdates();
  liveUpdates.connect({});
  streams[0].onerror();

  assert.equal(streams[0].closed, true);
  assert.equal(timers[0].delay, 2000);
  timers[0].callback();
  assert.equal(streams.length, 2);

  streams[1].onerror();
  assert.equal(timers[1].delay, 4000);
  timers[1].callback();
  streams[2].onopen();
  streams[2].onerror();
  assert.equal(timers[2].delay, 2000);
});

test('closing a stream cancels a pending reconnect and prevents reopening', () => {
  const { liveUpdates, streams, timers } = loadLiveUpdates();
  const connection = liveUpdates.connect({});
  streams[0].onerror();
  connection.close();

  assert.equal(timers[0].cleared, true);
  timers[0].callback();
  assert.equal(streams.length, 1);
});
