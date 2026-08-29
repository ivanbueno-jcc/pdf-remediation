'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function fakeElement() {
  return {
    children: [],
    classList: {
      add() {},
      contains() { return false; },
      remove() {},
      toggle() {},
    },
    dataset: {},
    addEventListener() {},
    append(...children) { this.children.push(...children); },
    appendChild(child) { this.children.push(child); return child; },
    prepend(...children) { this.children.unshift(...children); },
    removeAttribute() {},
    replaceChildren(...children) { this.children = children; },
    setAttribute() {},
  };
}

function loadStagingCode() {
  const elements = new Map();
  const document = {
    createElement: fakeElement,
    createElementNS: fakeElement,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, fakeElement());
      return elements.get(id);
    },
    querySelectorAll() { return []; },
  };
  const context = {
    console,
    document,
    requestAnimationFrame(callback) { callback(); },
    window: {
      clearTimeout() {},
      matchMedia() { return { matches: true }; },
      setTimeout() { return 1; },
    },
  };
  vm.createContext(context);

  const appPath = path.join(__dirname, '..', 'src', 'pdf_web', 'static', 'app.js');
  const source = fs.readFileSync(appPath, 'utf8');
  const functionsOnly = source.split('/* ---------- wiring ---------- */')[0];
  vm.runInContext(
    functionsOnly + '\nglobalThis.testApi = { addFiles, filteredRecentJobs, readinessPresentation, state };',
    context,
    { filename: appPath },
  );
  return { ...context.testApi, elements };
}

test('non-PDF selections are ignored instead of remaining staged', () => {
  const { addFiles, state, elements } = loadStagingCode();
  state.health = { can_submit: true };

  addFiles([
    { name: 'notes.txt', size: 12 },
    { name: 'document.PDF', size: 25 },
  ]);

  assert.deepEqual(
    Array.from(state.staged, (item) => item.file.name),
    ['document.PDF'],
  );
  assert.equal(state.staged[0].status, 'accepted');
  assert.match(elements.get('batch-announcer').textContent, /notes\.txt/);
  assert.match(
    elements.get('toast-region').children[0].textContent,
    /1 non-PDF file ignored/,
  );
});

test('job filters only return matching recent jobs', () => {
  const { filteredRecentJobs, state } = loadStagingCode();
  state.jobs = [
    { job_id: 'running', name: 'quarterly-report.pdf', status: 'running', outcome: null },
    { job_id: 'recent-match', name: 'quarterly-report.pdf', status: 'completed', outcome: 'remediated' },
    { job_id: 'recent-other', name: 'invoice.pdf', status: 'completed', outcome: 'remediated' },
  ];
  state.jobSearch = 'quarterly';
  state.jobOutcomeFilter = 'remediated';

  assert.deepEqual(
    Array.from(filteredRecentJobs(), (job) => job.job_id),
    ['recent-match'],
  );
});

test('readiness presentation distinguishes ready, limited, and unavailable states', () => {
  const { readinessPresentation } = loadStagingCode();

  assert.deepEqual(
    { ...readinessPresentation({ can_submit: true, checks: [] }, null) },
    { tone: 'ready', label: 'System ready' },
  );
  assert.deepEqual(
    { ...readinessPresentation({
      can_submit: true,
      checks: [{ ok: false, required: false }],
    }, null) },
    { tone: 'degraded', label: 'Limited availability' },
  );
  assert.deepEqual(
    { ...readinessPresentation({ can_submit: false, checks: [] }, null) },
    { tone: 'unavailable', label: 'System unavailable' },
  );
  assert.deepEqual(
    { ...readinessPresentation({ can_submit: false, checks: [] }, 'Sign in') },
    { tone: 'unavailable', label: 'Access unavailable' },
  );
});
