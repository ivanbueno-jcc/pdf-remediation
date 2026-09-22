'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function load(name) {
  const window = {};
  const context = { window };
  vm.createContext(context);
  const filename = path.join(__dirname, '..', 'src', 'pdf_web', 'static', name + '.js');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
  return window;
}

test('upload staging validation is usable without loading app.js or a DOM', () => {
  const { PdfWebUploadStaging } = load('upload-staging');
  const file = { name: 'report.pdf', size: 12 };
  const limits = { max_files: 2, max_file_bytes: 20, max_submission_bytes: 25 };
  assert.deepEqual(
    JSON.parse(JSON.stringify(PdfWebUploadStaging.validate(file, [], limits, 0, String))),
    { accepted: true },
  );
  assert.match(
    PdfWebUploadStaging.validate(file, [{ file }], limits, 0, String).reason,
    /already in the batch/,
  );
});

test('queue view keeps active jobs and caps the retained terminal window', () => {
  const { PdfWebQueueView } = load('queue-view');
  const jobs = [
    { job_id: 'old', status: 'completed', created_at: '2026-01-01' },
    { job_id: 'active', status: 'running', created_at: '2026-01-02' },
    { job_id: 'new', status: 'failed', created_at: '2026-01-03' },
  ];
  const retained = PdfWebQueueView.retainWindow(jobs, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(retained.map((job) => job.job_id))), ['new', 'active']);
});

test('job detail projections merge violations across validation profiles', () => {
  const { PdfWebJobDetail } = load('job-detail');
  const merged = PdfWebJobDetail.mergeViolations({ profiles: {
    ua1: { violations: [{ clause_test: '1.1', description: 'Missing title' }] },
    wcag: { violations: [{ clause_test: '1.1' }, { clause_test: '2.4' }] },
  } });
  assert.deepEqual(JSON.parse(JSON.stringify(merged)), [
    { clause_test: '1.1', description: 'Missing title', profiles: ['UA1', 'WCAG'] },
    { clause_test: '2.4', description: '', profiles: ['WCAG'] },
  ]);
});

test('job action policy delegates confirmation copy without loading the application', () => {
  const calls = [];
  const window = { PdfWebDialogs: {
    confirmAction(...args) { calls.push(args); return Promise.resolve(true); },
  } };
  const context = { window };
  vm.createContext(context);
  const filename = path.join(__dirname, '..', 'src', 'pdf_web', 'static', 'actions.js');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
  const actions = window.PdfWebActions.create({});
  return actions.confirmCancel({ name: 'sample.pdf', status: 'running' }).then((result) => {
    assert.equal(result, true);
    assert.match(calls[0][1], /next safe point/);
  });
});
