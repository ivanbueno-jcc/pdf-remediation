'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function fakeElement() {
  const classes = new Set();
  return {
    children: [],
    classList: {
      add(...names) { names.forEach((name) => classes.add(name)); },
      contains(name) { return classes.has(name); },
      remove(...names) { names.forEach((name) => classes.delete(name)); },
      toggle(name, force) {
        const enabled = force === undefined ? !classes.has(name) : force;
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
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
    functionsOnly + '\nglobalThis.testApi = { addFiles, appendViolationSection, buildJobRow, filteredRecentJobs, readinessPresentation, renderDetail, shouldToggleJobRow, shouldTogglePipelineStage, state, updateJobRow, updateSubmitState, validationComparison, validationRequirementLabel };',
    context,
    { filename: appPath },
  );
  return { ...context.testApi, elements };
}

test('Run options exposes each optional pipeline stage', () => {
  const indexPath = path.join(__dirname, '..', 'src', 'pdf_web', 'static', 'index.html');
  const html = fs.readFileSync(indexPath, 'utf8');

  [
    ['attempt-unlock', 'Remove security'],
    ['attempt-fix', 'Apply remediation'],
    ['attempt-font-fix', 'Font repair'],
    ['attempt-targeted-fixes', 'Targeted repairs'],
  ].forEach(([id, label]) => {
    assert.match(html, new RegExp('id="' + id + '" checked> ' + label));
  });
  assert.doesNotMatch(html, /class="stage-number"/);
  assert.match(html, /<legend>Pipeline stages<\/legend>/);
  assert.doesNotMatch(html, /pipeline-hint/);
  assert.doesNotMatch(html, /class="field-heading">Preset/);
  assert.match(html, /aria-label="Remediation preset"/);
  assert.match(html, /class="config-tooltip-trigger"/);
  assert.match(html, /id="config-description" role="tooltip"/);
  assert.match(html, /class="setting-option validation-option"/);
  assert.match(html, /id="require-wcag" checked> WCAG/);
  assert.match(html, /id="require-pdfua1"> PDF\/UA-1/);
  assert.doesNotMatch(html, /id="strict"/);
});

test('Validation change stores each selected profile combination', () => {
  const { validationRequirementLabel } = loadStagingCode();

  assert.equal(validationRequirementLabel('wcag only'), 'WCAG');
  assert.equal(validationRequirementLabel('pdfua1 only'), 'PDF/UA-1');
  assert.equal(validationRequirementLabel('wcag and pdfua1'), 'WCAG • PDF/UA-1');
});

test('validation requirement is merged with the outcome pill', () => {
  const { buildJobRow, updateJobRow } = loadStagingCode();
  const job = {
    job_id: 'job-1', name: 'document.pdf', created_at: '2026-08-31T12:00:00',
    page_count: 2, config_label: 'Standard', initially_secured: false,
    status: 'completed', outcome: 'remediated', outcome_label: 'Remediated',
    validation_requirement: 'wcag and pdfua1', before: null, after: null,
    current_stage: null, has_pdf: false,
  };
  const entry = buildJobRow(job);
  entry.status.querySelectorAll = () => [];

  updateJobRow(entry, job);

  assert.equal(entry.validationRequirement.textContent, 'WCAG • PDF/UA-1');
  assert.equal(entry.outcomeWrap.className, 'outcome-composite ok');
  assert.equal(entry.outcomeWrap.children[0], entry.outcome);
  assert.equal(entry.outcomeWrap.children[1], entry.validationRequirement);
  assert.doesNotMatch(entry.validation.innerHTML, /validation-requirement/);
});

test('submission is disabled when neither validation profile is selected', () => {
  const { addFiles, state, elements, updateSubmitState } = loadStagingCode();
  state.health = { can_submit: true };
  updateSubmitState();
  elements.get('require-wcag').checked = false;
  elements.get('require-pdfua1').checked = false;
  addFiles([{ name: 'document.pdf', size: 25 }]);

  updateSubmitState();

  assert.equal(elements.get('submit').disabled, true);
  assert.match(elements.get('submit-note').textContent, /Select WCAG, PDF\/UA-1/);
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

test('pipeline stage cards toggle outside their interactive controls', () => {
  const { shouldTogglePipelineStage } = loadStagingCode();
  const target = (selector) => ({ closest(candidate) {
    return candidate.includes(selector) ? {} : null;
  } });

  assert.equal(shouldTogglePipelineStage(target('.other-content')), true);
  assert.equal(shouldTogglePipelineStage(target('select')), false);
  assert.equal(shouldTogglePipelineStage(target('.stage-heading label')), false);
  assert.equal(shouldTogglePipelineStage(null), true);
});

test('job metadata marks only files that were initially secured', () => {
  const { buildJobRow } = loadStagingCode();
  const job = {
    job_id: 'job-1', name: 'document.pdf', created_at: '2026-08-31T12:00:00',
    page_count: 2, config_label: 'Standard', initially_secured: true,
  };
  const securedEntry = buildJobRow(job);
  const securedMeta = securedEntry.row.children[0].children[0].children[1].children[1];
  const marker = securedMeta.children.at(-1);

  assert.equal(marker.className, 'job-meta-security');
  assert.equal(marker.title, 'Initially secured');
  assert.equal(marker.children[0].classList.contains('job-meta-lock'), true);

  const plainEntry = buildJobRow({ ...job, initially_secured: false });
  const plainMeta = plainEntry.row.children[0].children[0].children[1].children[1];
  assert.equal(plainMeta.children.some((child) => child.className === 'job-meta-security'), false);
});

test('job metadata adds the lock when polling discovers initial security', () => {
  const { buildJobRow, updateJobRow } = loadStagingCode();
  const job = {
    job_id: 'job-1', name: 'document.pdf', created_at: '2026-08-31T12:00:00',
    page_count: 2, config_label: 'Standard', initially_secured: false,
    status: 'running', outcome: null, outcome_label: null, before: null, after: null,
    current_stage: 'validate_before', has_pdf: false,
  };
  const entry = buildJobRow(job);
  entry.status.querySelectorAll = () => [];
  updateJobRow(entry, { ...job, initially_secured: true });

  assert.equal(
    entry.meta.children.some((child) => child.className === 'job-meta-security'), true
  );
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
