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

function loadStagingCode(fetchImpl) {
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
    fetch: fetchImpl,
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
    functionsOnly + '\nglobalThis.testApi = { addFiles, appendViolationSection, filteredRecentJobs, readinessPresentation, renderDetail, shouldToggleJobRow, state };',
    context,
    { filename: appPath },
  );
  return { ...context.testApi, elements };
}

test('Run options exposes each optional pipeline stage', () => {
  const indexPath = path.join(__dirname, '..', 'src', 'pdf_web', 'static', 'index.html');
  const html = fs.readFileSync(indexPath, 'utf8');

  [
    ['attempt-unlock', 'Remove security', 1],
    ['attempt-fix', 'Apply remediation', 2],
    ['attempt-font-fix', 'Font repair', 3],
    ['attempt-targeted-fixes', 'Targeted repairs', 4],
  ].forEach(([id, label, stage]) => {
    assert.match(html, new RegExp('id="' + id + '" checked> ' + label));
    assert.match(html, new RegExp('class="stage-number" aria-hidden="true">' + stage));
  });
  assert.match(html, /<legend>Pipeline stages<\/legend>/);
  assert.match(html, /<span class="field-heading">Preset<\/span>/);
  assert.match(html, /class="setting-option validation-option"/);
});

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

test('job row clicks ignore interactive controls', () => {
  const { shouldToggleJobRow } = loadStagingCode();
  const target = (interactive) => ({ closest() { return interactive ? {} : null; } });

  assert.equal(shouldToggleJobRow(target(false)), true);
  assert.equal(shouldToggleJobRow(target(true)), false);
  assert.equal(shouldToggleJobRow(null), true);
});

test('job details render pipeline stages beside the violations pane', () => {
  const { renderDetail } = loadStagingCode();
  const cell = fakeElement();
  cell.parentElement = { querySelector() { return null; } };

  renderDetail(cell, {
    job_id: 'job-1',
    stages: [{ name: 'initial_validation', status: 'ok', detail: 'Complete' }],
    warnings: ['Review the source document.'],
  });

  const detail = cell.children[0];
  const layout = detail.children[0];
  const [sidebar, violations] = layout.children;
  assert.equal(detail.className, 'job-detail');
  assert.equal(layout.className, 'job-detail-layout');
  assert.equal(sidebar.className, 'job-detail-sidebar');
  assert.equal(sidebar.children[0].textContent, 'Pipeline stages');
  assert.equal(sidebar.children[1].className, 'stages');
  assert.equal(sidebar.children[2].textContent, 'Review the source document.');
  assert.equal(violations.className, 'job-detail-violations');
});

test('resolved violations expand by default only for remediated files', async () => {
  const before = {
    profiles: {
      wcag: { violations: [{ clause_test: '1.1.1-1', description: 'Missing text alternative' }] },
    },
  };
  const after = { profiles: { wcag: { violations: [] } } };
  const renderForOutcome = async (outcome) => {
    let reportIndex = 0;
    const reports = [before, after];
    const fetchImpl = async () => ({
      ok: true,
      async json() { return reports[reportIndex++]; },
    });
    const { appendViolationSection } = loadStagingCode(fetchImpl);
    const wrap = fakeElement();
    appendViolationSection(wrap, {
      job_id: 'job-1', before: true, after: true, outcome,
    });
    await new Promise((resolve) => setImmediate(resolve));
    return wrap.children[1].children[0];
  };

  const remediated = await renderForOutcome('remediated');
  const improved = await renderForOutcome('improved');
  assert.equal(remediated.className, 'violation-collapse');
  assert.equal(remediated.open, true);
  assert.equal(improved.open, false);
});
