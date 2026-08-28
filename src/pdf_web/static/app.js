'use strict';

const POLL_MS = 2000;

const state = {
  staged: [],
  submitting: false,
  uploadLimits: {
    max_files: 200,
    max_file_bytes: 200 * 1024 * 1024,
    max_submission_bytes: 2 * 1024 * 1024 * 1024,
  },
  health: null,
  authError: null,
  openJobId: null,
  openDownloadJobId: null,
  pollTimer: null,
  jobs: [],
  jobStatusSnapshot: null,
  jobSearch: '',
  jobStatusFilter: 'all',
};

const el = (id) => document.getElementById(id);

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024, index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return value.toFixed(value >= 10 ? 0 : 1) + ' ' + units[index];
}

function describeError(payload) {
  const detail = payload && payload.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((e) => e.msg || JSON.stringify(e)).join('; ');
  return 'Request failed.';
}

/* ---------- environment ---------- */

async function loadHealth() {
  try {
    const response = await fetch('/api/health');
    if (response.status === 401 || response.status === 403) {
      const payload = await response.json().catch(() => ({}));
      state.authError = describeError(payload);
      state.health = { checks: [], can_submit: false, blocking: [] };
    } else {
      state.authError = null;
      state.health = await response.json();
    }
  } catch (error) {
    state.authError = null;
    state.health = { checks: [], can_submit: false, blocking: ['server unreachable'] };
  }
  renderIdentity();
  renderHealth();
  updateSubmitState();
}

function renderIdentity() {
  const line = el('identity');
  const auth = (state.health && state.health.auth) || {};
  if (state.authError || !state.health.user) { line.textContent = ''; return; }
  line.textContent = auth.multi_user
    ? 'Signed in as ' + state.health.user + ' · your jobs are private to you'
    : 'Single-user mode · ' + state.health.user;
}

function renderHealth() {
  const checks = state.health.checks || [];
  const missing = checks.filter((check) => !check.ok);
  const section = el('health-section');

  // Nothing to tell the user: hide the whole section rather than showing an
  // all-green checklist nobody needs to read.
  const showSection = Boolean(state.authError) || missing.length > 0 ||
    !state.health.can_submit;
  section.classList.toggle('hidden', !showSection);
  if (!showSection) return;

  const container = el('health-chips');
  container.innerHTML = '';
  missing.forEach((check) => {
    const chip = document.createElement('span');
    chip.className = 'chip ' + (check.required ? 'bad' : 'warn');
    chip.innerHTML = '<span class="dot"></span>';
    chip.append(check.name + ' — ' + check.detail);
    chip.title = check.detail;
    container.appendChild(chip);
  });

  const banner = el('health-banner');
  if (state.authError) {
    banner.className = 'banner bad';
    banner.textContent = state.authError;
    return;
  }
  if (!state.health.can_submit) {
    banner.className = 'banner bad';
    banner.textContent = 'Cannot run: ' + (state.health.blocking || []).join(', ') +
      '. Validation would report every file as unvalidatable, so submitting is disabled.';
  } else if (state.health.recommend_skip_font_fix) {
    banner.className = 'banner warn';
    banner.textContent = 'Docker is unavailable, so font repair will be skipped. ' +
      'Everything else still runs.';
  } else {
    banner.className = 'banner hidden';
  }
}

function renderConfigDescription() {
  const option = el('config-select').selectedOptions[0];
  el('config-description').textContent = option ? option.dataset.description || '' : '';
}

async function loadConfigs() {
  const select = el('config-select');
  try {
    const payload = await (await fetch('/api/config-files')).json();
    if (payload.upload_limits) state.uploadLimits = payload.upload_limits;
    renderUploadGuidance();
    select.innerHTML = '';
    payload.files.forEach((entry) => {
      const option = document.createElement('option');
      option.value = entry.name;
      option.textContent = (entry.label || entry.name) +
        (entry.available ? '' : ' (unavailable)');
      option.dataset.description = entry.description || '';
      option.title = entry.description || '';
      option.disabled = !entry.available;
      option.selected = entry.name === payload.default;
      select.appendChild(option);
    });
    renderConfigDescription();
  } catch (error) {
    select.innerHTML = '<option value="default.json">Standard remediation</option>';
    select.options[0].dataset.description =
      'Runs the full remediation preset for the broadest automatic accessibility repair coverage.';
    renderConfigDescription();
    renderUploadGuidance();
  }
}

/* ---------- staging ---------- */

function renderUploadGuidance() {
  const limits = state.uploadLimits;
  el('upload-guidance').textContent = 'PDF only · up to ' + limits.max_files +
    ' files · ' + formatBytes(limits.max_file_bytes) + ' each · ' +
    formatBytes(limits.max_submission_bytes) + ' total';
}

function acceptedItems() {
  return state.staged.filter((item) => item.status === 'accepted');
}

function announceSelectionErrors(errors) {
  const announcer = el('batch-announcer');
  announcer.textContent = '';
  if (!errors.length) return;
  requestAnimationFrame(() => {
    announcer.textContent = errors.length + (errors.length === 1
      ? ' file needs attention. ' : ' files need attention. ') +
      errors.map((error) => error.name + ': ' + error.reason).join(' ');
  });
}

function addFiles(fileList) {
  if (state.submitting) return;
  const previousCount = acceptedItems().length;
  const errors = [];
  let ready = acceptedItems();
  let totalBytes = ready.reduce((sum, item) => sum + item.file.size, 0);
  const limits = state.uploadLimits;
  Array.from(fileList).forEach((file) => {
    let reason = '';
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      reason = 'Only PDF files are accepted.';
    } else if (ready.some((item) => item.file.name === file.name && item.file.size === file.size)) {
      reason = 'This file is already in the batch.';
    } else if (file.size > limits.max_file_bytes) {
      reason = 'File exceeds the ' + formatBytes(limits.max_file_bytes) + ' per-file limit.';
    } else if (ready.length >= limits.max_files) {
      reason = 'The batch already contains the maximum of ' + limits.max_files + ' files.';
    } else if (totalBytes + file.size > limits.max_submission_bytes) {
      reason = 'Adding it would exceed the ' +
        formatBytes(limits.max_submission_bytes) + ' batch limit.';
    }

    if (reason) {
      state.staged.push({ file, status: 'error', reason });
      errors.push({ name: file.name, reason });
      return;
    }

    const item = { file, status: 'accepted', reason: 'Ready to upload.' };
    state.staged.push(item);
    ready.push(item);
    totalBytes += file.size;
  });
  announceSelectionErrors(errors);
  if (acceptedItems().length > previousCount) clearSubmissionResult();
  renderStaged();
  updateSubmitState();
}

function batchStatusLabel(status) {
  return { accepted: 'Ready', error: 'Needs attention', uploading: 'Uploading',
           queued: 'Queued' }[status] || status;
}

function renderStaged() {
  const body = el('staged-body');
  body.innerHTML = '';
  const empty = state.staged.length === 0;
  el('staged-table').classList.toggle('hidden', empty);
  el('batch-toolbar').classList.toggle('hidden', empty);

  state.staged.forEach((item, index) => {
    const row = document.createElement('tr');
    row.dataset.status = item.status;
    const name = document.createElement('td');
    name.className = 'name';
    name.textContent = item.file.name;
    const size = document.createElement('td');
    size.className = 'batch-size';
    size.textContent = formatBytes(item.file.size);
    const status = document.createElement('td');
    status.className = 'batch-state';
    const label = document.createElement('span');
    label.className = 'batch-status ' + item.status;
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.setAttribute('aria-hidden', 'true');
    label.append(dot, batchStatusLabel(item.status));
    const reason = document.createElement('div');
    reason.className = 'batch-reason';
    reason.textContent = item.reason || '';
    status.append(label, reason);
    const action = document.createElement('td');
    action.className = 'batch-action';
    const remove = document.createElement('button');
    remove.className = 'small';
    remove.textContent = item.status === 'queued' ? 'Dismiss' : 'Remove';
    remove.disabled = state.submitting;
    remove.setAttribute('aria-label', remove.textContent + ' ' + item.file.name);
    remove.addEventListener('click', () => {
      state.staged.splice(index, 1);
      renderStaged();
      updateSubmitState();
    });
    action.appendChild(remove);
    row.append(name, size, status, action);
    body.appendChild(row);
  });

  const counts = state.staged.reduce((result, item) => {
    result[item.status] = (result[item.status] || 0) + 1;
    return result;
  }, {});
  const ready = acceptedItems();
  const total = ready.reduce((sum, item) => sum + item.file.size, 0);
  const parts = [];
  if (counts.accepted) parts.push(counts.accepted + ' ready (' + formatBytes(total) + ')');
  if (counts.uploading) parts.push(counts.uploading + ' uploading');
  if (counts.queued) parts.push(counts.queued + ' queued');
  if (counts.error) parts.push(counts.error + ' need attention');
  el('staged-total').textContent = parts.join(' · ');
}

function updateSubmitState(message) {
  const healthy = Boolean(state.health && state.health.can_submit);
  const readyCount = acceptedItems().length;
  const submit = el('submit');
  submit.disabled = state.submitting || !(healthy && readyCount);
  submit.textContent = state.submitting ? 'Uploading…'
    : (readyCount === 1 ? 'Run 1 job'
      : (readyCount > 1 ? 'Run ' + readyCount + ' jobs' : 'Run pipeline'));
  el('file-input').disabled = state.submitting;
  el('drop').setAttribute('aria-disabled', state.submitting ? 'true' : 'false');
  el('clear-staged').disabled = state.submitting;
  if (message !== undefined) el('submit-note').textContent = message;
  else if (state.submitting) el('submit-note').textContent = 'Uploading PDFs and creating jobs…';
  else if (!healthy) el('submit-note').textContent = 'Fix the environment problems above first.';
  else if (!readyCount) el('submit-note').textContent = state.staged.some(
    (item) => item.status === 'error'
  ) ? 'Remove files that need attention or add a valid PDF.'
    : (state.staged.length ? 'Add another PDF to start a new batch.' : 'Add at least one PDF.');
  else el('submit-note').textContent = '';
}

function clearSubmissionResult() {
  const result = el('submit-result');
  result.className = 'banner hidden';
  result.textContent = '';
  result.removeAttribute('role');
  result.removeAttribute('aria-live');
}

function showSubmissionResult(message, tone) {
  const result = el('submit-result');
  result.textContent = message;
  result.className = 'banner ' + tone;
  result.setAttribute('role', tone === 'bad' ? 'alert' : 'status');
  result.setAttribute('aria-live', tone === 'bad' ? 'assertive' : 'polite');
}

/* ---------- submit ---------- */

async function submitJobs() {
  const submitted = acceptedItems();
  const form = new FormData();
  submitted.forEach((item) => form.append('files', item.file, item.file.name));
  form.append('config_file', el('config-select').value);
  form.append('skip_font_fix', el('skip-font-fix').checked ? 'true' : 'false');
  form.append('wcag_and_ua1_must_pass', el('strict').checked ? 'true' : 'false');

  state.submitting = true;
  submitted.forEach((item) => {
    item.status = 'uploading';
    item.reason = 'Uploading to the server…';
  });
  clearSubmissionResult();
  renderStaged();
  updateSubmitState();

  let payload;
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body: form });
    payload = await response.json();
    if (!response.ok) {
      const rejected = payload.rejected || [];
      submitted.forEach((item) => {
        const rejection = rejected.find((entry) => entry.original_name === item.file.name);
        item.status = rejection ? 'error' : 'accepted';
        item.reason = rejection ? rejection.reason : 'Ready to try again.';
      });
      throw new Error(describeError(payload));
    }
  } catch (error) {
    state.submitting = false;
    submitted.filter((item) => item.status === 'uploading').forEach((item) => {
      item.status = 'accepted';
      item.reason = 'Upload did not finish. Ready to try again.';
    });
    renderStaged();
    updateSubmitState('');
    showSubmissionResult(String(error.message || error), 'bad');
    return;
  }

  state.submitting = false;
  submitted.forEach((item) => {
    const rejection = (payload.rejected || []).find(
      (entry) => entry.original_name === item.file.name
    );
    const job = (payload.jobs || []).find(
      (entry) => entry.file && entry.file.original_name === item.file.name
    );
    item.status = rejection ? 'error' : (job ? 'queued' : 'error');
    item.reason = rejection ? rejection.reason : (job ? 'Job queued.' : 'No job was created.');
    item.jobId = job ? job.job_id : null;
  });
  // Processing has started; remove queued files from the staging area. Keep
  // rejected files visible so the user can fix and resubmit them.
  state.staged = state.staged.filter((item) => item.status === 'error');
  el('batch-announcer').textContent = '';
  renderStaged();
  const queuedMessage = (payload.jobs.length === 1 ? '1 job queued' : payload.jobs.length + ' jobs queued') +
    ' · ' + payload.concurrency +
    ' run at a time, ' + payload.your_limit + ' of yours at once.';
  updateSubmitState('');
  showSubmissionResult(queuedMessage, 'ok');
  startPolling();
}

/* ---------- the job list ---------- */

function startPolling() {
  if (state.pollTimer) return;
  refreshQueue();
  state.pollTimer = setInterval(refreshQueue, POLL_MS);
}

function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

async function refreshQueue() {
  let payload;
  try {
    const response = await fetch('/api/queue');
    if (!response.ok) { stopPolling(); return; }
    payload = await response.json();
  } catch (error) { return; }

  const jobs = payload.jobs || [];
  announceJobChanges(jobs);
  state.jobs = jobs;
  el('jobs-section').classList.toggle('hidden', state.jobs.length === 0);
  el('queue-summary').textContent = state.jobs.length
    ? payload.your_running + ' of ' + payload.your_limit +
      ' running · ' + payload.concurrency + ' processed at a time'
    : '';
  renderJobs();

  // Nothing is moving, so stop asking.
  if (payload.all_terminal) stopPolling();
}

function announceStatus(message) {
  const region = el('job-announcer');
  region.textContent = '';
  requestAnimationFrame(() => { region.textContent = message; });
}

function jobStatusAnnouncement(job) {
  if (job.status === 'running') {
    const stage = job.current_stage ? ' at ' + job.current_stage.replaceAll('_', ' ') : '';
    return job.name + ' is running' + stage + '.';
  }
  if (job.status === 'completed') {
    return job.name + ' processing complete. Accessibility outcome: ' +
      (job.outcome_label || 'available') + '.';
  }
  if (job.status === 'failed') return job.name + ' processing failed.';
  if (job.status === 'cancelled') return job.name + ' was cancelled.';
  return job.name + ' is queued.';
}

function announceJobChanges(jobs) {
  const next = {};
  const messages = [];
  jobs.forEach((job) => {
    next[job.job_id] = { status: job.status, outcome: job.outcome };
    if (!state.jobStatusSnapshot || !state.jobStatusSnapshot[job.job_id]) return;
    const previous = state.jobStatusSnapshot[job.job_id];
    if (previous.status !== job.status) messages.push(jobStatusAnnouncement(job));
    else if (previous.outcome !== job.outcome && job.outcome_label) {
      messages.push('Accessibility outcome for ' + job.name + ': ' + job.outcome_label + '.');
    }
  });
  state.jobStatusSnapshot = next;
  if (messages.length) announceStatus(messages.join(' '));
}

function isActiveJob(job) {
  return job.status === 'queued' || job.status === 'running';
}

function filteredJobs() {
  const query = state.jobSearch.trim().toLowerCase();
  const filter = state.jobStatusFilter;
  return state.jobs.filter((job) => {
    if (query && !String(job.name || '').toLowerCase().includes(query)) return false;
    if (filter === 'active' && !isActiveJob(job)) return false;
    if (filter !== 'all' && filter !== 'active' && job.status !== filter) return false;
    return true;
  });
}

function formatJobTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  }).format(date);
}

function renderJobGroup(groupId, bodyId, countId, jobs) {
  el(groupId).classList.toggle('hidden', jobs.length === 0);
  el(countId).textContent = jobs.length ? '(' + jobs.length + ')' : '';
  renderJobRows(el(bodyId), jobs);
}

function renderJobs() {
  const visible = filteredJobs();
  const active = visible.filter(isActiveJob);
  const recent = visible.filter((job) => !isActiveJob(job));
  const total = state.jobs.length;
  const empty = el('job-empty');
  empty.classList.toggle('hidden', visible.length > 0);
  empty.textContent = total
    ? 'No jobs match the current search and status filter.'
    : 'No jobs yet.';
  renderJobGroup('active-jobs-group', 'active-jobs-body', 'active-jobs-count', active);
  renderJobGroup('recent-jobs-group', 'recent-jobs-body', 'recent-jobs-count', recent);
}

function renderJobRows(body, jobs) {
  const open = state.openJobId;
  body.innerHTML = '';

  jobs.forEach((job) => {
    const row = document.createElement('tr');
    row.className = 'job-row';
    row.dataset.jobId = job.job_id;

    const name = document.createElement('td');
    name.className = 'name';
    const fileLabel = document.createElement('div');
    fileLabel.className = 'file-label';
    const disclosure = document.createElement('button');
    disclosure.type = 'button';
    disclosure.className = 'disclosure';
    disclosure.setAttribute('aria-expanded', 'false');
    disclosure.setAttribute('aria-label', 'Show details for ' + job.name);
    const caret = document.createElement('span');
    caret.className = 'caret';
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = '›';
    disclosure.appendChild(caret);
    const fileInfo = document.createElement('div');
    fileInfo.className = 'file-info';
    const fileName = document.createElement('span');
    fileName.className = 'file-name';
    fileName.textContent = job.name;
    fileInfo.appendChild(fileName);
    const metadata = [formatJobTimestamp(job.created_at), job.config_label || job.config_file]
      .filter(Boolean);
    if (metadata.length) {
      const meta = document.createElement('div');
      meta.className = 'job-meta';
      meta.textContent = metadata.join(' · ');
      fileInfo.appendChild(meta);
    }
    fileLabel.append(disclosure, fileInfo);
    name.appendChild(fileLabel);

    const processing = document.createElement('td');
    processing.className = 'processing';
    const processingState = document.createElement('span');
    processingState.className = 'processing-state ' + job.status;
    const processingDot = document.createElement('span');
    processingDot.className = 'dot';
    processingDot.setAttribute('aria-hidden', 'true');
    processingState.append(processingDot, processingLabel(job.status));
    processing.appendChild(processingState);
    const processingNote = processingDetail(job);
    if (processingNote) {
      const note = document.createElement('div');
      note.className = 'processing-detail';
      note.textContent = processingNote;
      processing.appendChild(note);
    }

    const status = document.createElement('td');
    status.className = 'job-status';
    const outcome = document.createElement('span');
    outcome.className = 'outcome ' + (job.outcome ? outcomeTone(job.outcome) : 'pending');
    outcome.textContent = job.outcome_label || 'Pending result';
    status.appendChild(outcome);
    if (job.error) {
      const note = document.createElement('div');
      note.className = 'muted';
      note.textContent = job.error;
      status.appendChild(note);
    }

    const validation = document.createElement('td');
    validation.className = 'validation-change';
    validation.innerHTML = validationComparison(job.before, job.after);

    const downloads = document.createElement('td');
    downloads.className = 'actions';
    const base = '/api/jobs/' + job.job_id + '/';
    const split = document.createElement('div');
    split.className = 'split-download';
    const primary = link(base + 'pdf', 'Remediated PDF', job.has_pdf,
      job.name, 'file-check');
    primary.classList.add('split-primary');
    split.appendChild(primary);

    const menu = document.createElement('details');
    menu.className = 'download-menu';
    menu.dataset.jobId = job.job_id;
    menu.open = state.openDownloadJobId === job.job_id;
    const menuSummary = document.createElement('summary');
    menuSummary.setAttribute('aria-label', 'More download options for ' + job.name);
    menuSummary.setAttribute('aria-expanded', menu.open ? 'true' : 'false');
    const menuId = 'downloads-' + job.job_id;
    menuSummary.setAttribute('aria-controls', menuId);
    const menuCaret = document.createElement('span');
    menuCaret.className = 'menu-caret';
    menuCaret.setAttribute('aria-hidden', 'true');
    menuCaret.textContent = '▾';
    menuSummary.append(menuCaret);
    const popover = document.createElement('div');
    popover.className = 'download-popover';
    popover.id = menuId;
    popover.appendChild(link(base + 'before', 'Before report', Boolean(job.before),
      job.name, 'file-search'));
    popover.appendChild(link(base + 'after', 'After report', Boolean(job.after),
      job.name, 'clipboard-check'));
    popover.appendChild(link(base + 'download', 'Download all',
      job.status === 'completed', job.name, 'download'));
    menu.append(menuSummary, popover);
    menu.addEventListener('toggle', () => {
      menuSummary.setAttribute('aria-expanded', menu.open ? 'true' : 'false');
      if (menu.open) {
        state.openDownloadJobId = job.job_id;
        document.querySelectorAll('.download-menu[open]').forEach((other) => {
          if (other !== menu) other.removeAttribute('open');
        });
      } else if (state.openDownloadJobId === job.job_id) {
        state.openDownloadJobId = null;
      }
    });
    split.appendChild(menu);
    downloads.appendChild(split);

    row.append(name, processing, status, validation, downloads);
    body.appendChild(row);

    const detail = document.createElement('tr');
    detail.className = 'detail-row hidden';
    detail.id = 'job-details-' + job.job_id;
    detail.setAttribute('aria-hidden', 'true');
    disclosure.setAttribute('aria-controls', detail.id);
    const cell = document.createElement('td');
    cell.colSpan = 5;
    detail.appendChild(cell);
    body.appendChild(detail);

    const toggle = () => toggleJob(job, row, detail, cell, disclosure);
    disclosure.addEventListener('click', toggle);

    if (open === job.job_id) toggleJob(job, row, detail, cell, disclosure, true);
  });
}

function processingLabel(status) {
  return { queued: 'Queued', running: 'Running', completed: 'Complete',
           failed: 'Failed', cancelled: 'Cancelled' }[status] || status;
}

function processingDetail(job) {
  if (job.status === 'queued') {
    if (job.jobs_ahead === 0 || job.jobs_ahead === null) return 'Next to run';
    return job.jobs_ahead + ' ahead';
  }
  if (job.status === 'running' && job.current_stage) {
    return job.current_stage.replaceAll('_', ' ');
  }
  return '';
}

function outcomeTone(outcome) {
  if (outcome === 'remediated' || outcome === 'already_compliant') return 'ok';
  if (outcome === 'improved' || outcome === 'unchanged' || outcome === 'cancelled') return 'warn';
  return 'bad';
}

function validationValue(entry) {
  if (!entry) return { text: 'Pending', tone: 'pending' };
  if (entry.status === 'pass') return { text: 'Pass', tone: 'ok' };
  if (entry.status === 'error') return { text: 'Error', tone: 'warn' };
  const count = Number(entry.failed_rules_count || 0);
  return { text: count + ' fail' + (count === 1 ? '' : 's'), tone: 'bad' };
}

function validationComparison(before, after) {
  if (!before && !after) return '<span class="muted">Waiting for validation…</span>';
  return '<div class="validation-summary">' + ['ua1', 'wcag'].map((profile) => {
    const label = profile === 'ua1' ? 'UA1' : 'WCAG';
    const beforeValue = validationValue(((before || {}).profiles || {})[profile]);
    const afterValue = validationValue(((after || {}).profiles || {})[profile]);
    return '<div class="validation-line"><span class="validation-profile">' + label +
      '</span><span class="validation-value ' + beforeValue.tone + '">' + beforeValue.text +
      '</span><span class="validation-arrow" aria-hidden="true">→</span>' +
      '<span class="validation-value ' + afterValue.tone + '">' + afterValue.text + '</span></div>';
  }).join('') + '</div>';
}

function downloadIcon(name) {
  const namespace = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(namespace, 'svg');
  svg.classList.add('dl-icon');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');

  const shapes = {
    'file-check': [
      ['path', { d: 'M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z' }],
      ['polyline', { points: '14 2 14 8 20 8' }],
      ['path', { d: 'm9 15 2 2 4-4' }],
    ],
    'file-search': [
      ['path', { d: 'M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z' }],
      ['polyline', { points: '14 2 14 8 20 8' }],
      ['circle', { cx: '11.5', cy: '14.5', r: '2.5' }],
      ['path', { d: 'm13.3 16.3 2.2 2.2' }],
    ],
    'clipboard-check': [
      ['rect', { width: '8', height: '4', x: '8', y: '2', rx: '1' }],
      ['path', { d: 'M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2' }],
      ['path', { d: 'm9 14 2 2 4-4' }],
    ],
    download: [
      ['path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }],
      ['polyline', { points: '7 10 12 15 17 10' }],
      ['line', { x1: '12', x2: '12', y1: '15', y2: '3' }],
    ],
  };

  (shapes[name] || []).forEach(([tag, attributes]) => {
    const shape = document.createElementNS(namespace, tag);
    Object.entries(attributes).forEach(([key, value]) => shape.setAttribute(key, value));
    svg.appendChild(shape);
  });
  return svg;
}

function link(href, label, enabled, fileName, iconName) {
  const anchor = document.createElement('a');
  anchor.className = 'dl';
  anchor.append(downloadIcon(iconName), label);
  anchor.setAttribute('aria-label', label + ' for ' + fileName +
    (enabled ? '' : ' (unavailable)'));
  if (enabled) {
    anchor.href = href;
  } else {
    anchor.setAttribute('aria-disabled', 'true');
    anchor.title = 'Unavailable until this artifact is ready';
  }
  return anchor;
}

/* ---------- job detail ---------- */

function updateDisclosure(disclosure, fileName, expanded) {
  disclosure.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  disclosure.setAttribute('aria-label', (expanded ? 'Hide' : 'Show') +
    ' details for ' + fileName);
}

async function toggleJob(job, row, detailRow, cell, disclosure, forceOpen) {
  const isOpen = row.dataset.open === 'true';
  if (isOpen && !forceOpen) {
    row.dataset.open = 'false';
    detailRow.classList.add('hidden');
    detailRow.setAttribute('aria-hidden', 'true');
    updateDisclosure(disclosure, job.name, false);
    state.openJobId = null;
    return;
  }

  state.openJobId = job.job_id;
  row.dataset.open = 'true';
  detailRow.classList.remove('hidden');
  detailRow.setAttribute('aria-hidden', 'false');
  updateDisclosure(disclosure, job.name, true);
  cell.innerHTML = '<div class="job-detail muted" role="status">Loading job details…</div>';

  try {
    const response = await fetch('/api/jobs/' + job.job_id);
    if (!response.ok) throw new Error('Details request failed.');
    const detailJob = await response.json();
    renderDetail(cell, detailJob);
    if (row.dataset.open === 'true') announceStatus('Details loaded for ' + job.name + '.');
  } catch (error) {
    cell.innerHTML = '<div class="job-detail" role="alert">Job details are unavailable.</div>';
  }
}

function renderDetail(cell, job) {
  cell.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'job-detail';

  const stagesHeading = document.createElement('h4');
  stagesHeading.textContent = 'Pipeline stages';
  wrap.appendChild(stagesHeading);

  const list = document.createElement('ol');
  list.className = 'stages';
  (job.stages || []).forEach((stage) => {
    const item = document.createElement('li');
    item.dataset.status = stage.status;
    const marker = document.createElement('span');
    marker.className = 'marker';
    marker.textContent = stage.status === 'ok' ? '✓'
      : (stage.status === 'failed' ? '✕' : '–');
    const label = document.createElement('span');
    label.textContent = stage.name;
    const detail = document.createElement('span');
    detail.className = 'detail';
    detail.textContent = stage.detail || '';
    item.append(marker, label, detail);
    list.appendChild(item);
  });
  wrap.appendChild(list);

  (job.warnings || []).forEach((warning) => {
    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = warning;
    wrap.appendChild(note);
  });

  appendViolationSection(wrap, job);

  if (job.status === 'queued' || job.status === 'running') {
    const cancel = document.createElement('button');
    cancel.className = 'small';
    cancel.textContent = 'Cancel job';
    cancel.addEventListener('click', async (event) => {
      event.stopPropagation();
      cancel.disabled = true;
      await fetch('/api/jobs/' + job.job_id + '/cancel', { method: 'POST' });
      refreshQueue();
    });
    wrap.appendChild(cancel);
  }

  cell.appendChild(wrap);
}

function violationItem(violation) {
  const item = document.createElement('li');
  const code = document.createElement('code');
  code.textContent = violation.clause_test;
  item.appendChild(code);
  violation.profiles.forEach((profile) => {
    const tag = document.createElement('span');
    tag.className = 'profile-chip';
    tag.textContent = profile;
    item.appendChild(tag);
  });
  item.appendChild(document.createTextNode(' ' + (violation.description || '')));
  return item;
}

function violationGroupList(violations, tone) {
  const list = document.createElement('ul');
  list.className = 'violation-group violation-group-' + tone;
  violations.forEach((violation) => list.appendChild(violationItem(violation)));
  return list;
}

function violationList(jobId, stage) {
  const list = document.createElement('ul');
  list.innerHTML = '<li class="muted">Loading…</li>';
  fetch('/api/jobs/' + jobId + '/' + stage)
    .then((response) => (response.ok ? response.json() : null))
    .then((report) => {
      list.innerHTML = '';
      const merged = mergeViolations(report);
      if (!merged.length) {
        const none = document.createElement('li');
        none.className = 'none';
        none.textContent = 'No violations reported.';
        list.appendChild(none);
        return;
      }
      merged.forEach((violation) => list.appendChild(violationItem(violation)));
    })
    .catch(() => { list.innerHTML = '<li class="muted">Unavailable.</li>'; });
  return list;
}

/*
 * Once both a before and an after report exist, show one diffed view instead
 * of two near-duplicate lists: what's still failing, what's new, and what
 * got fixed (collapsed, since it's not actionable).
 */
function appendViolationSection(wrap, job) {
  const hasBefore = Boolean(job.before);
  const hasAfter = Boolean(job.after);
  if (!hasBefore && !hasAfter) return;

  if (hasBefore && hasAfter) {
    const heading = document.createElement('h4');
    heading.textContent = 'Accessibility violations';
    wrap.appendChild(heading);
    wrap.appendChild(violationDiff(job.job_id));
    return;
  }

  const stage = hasBefore ? 'before' : 'after';
  const heading = document.createElement('h4');
  heading.textContent = stage === 'before' ? 'Before remediation' : 'After remediation';
  wrap.appendChild(heading);
  wrap.appendChild(violationList(job.job_id, stage));
}

function violationDiff(jobId) {
  const container = document.createElement('div');
  container.className = 'violation-diff';
  container.innerHTML = '<p class="muted">Loading…</p>';

  Promise.all([
    fetch('/api/jobs/' + jobId + '/before').then((r) => (r.ok ? r.json() : null)),
    fetch('/api/jobs/' + jobId + '/after').then((r) => (r.ok ? r.json() : null)),
  ]).then(([beforeReport, afterReport]) => {
    container.innerHTML = '';

    const beforeMap = new Map(mergeViolations(beforeReport).map((v) => [v.clause_test, v]));
    const afterMap = new Map(mergeViolations(afterReport).map((v) => [v.clause_test, v]));
    const byCode = (a, b) => a.clause_test.localeCompare(b.clause_test);

    const persisting = [...beforeMap.values()].filter((v) => afterMap.has(v.clause_test)).sort(byCode);
    const resolved = [...beforeMap.values()].filter((v) => !afterMap.has(v.clause_test)).sort(byCode);
    const introduced = [...afterMap.values()].filter((v) => !beforeMap.has(v.clause_test)).sort(byCode);

    if (!persisting.length && !resolved.length && !introduced.length) {
      const none = document.createElement('p');
      none.className = 'none';
      none.textContent = 'No violations reported.';
      container.appendChild(none);
      return;
    }

    if (persisting.length) {
      const label = document.createElement('p');
      label.className = 'violation-group-label violation-group-label-bad';
      label.textContent = 'Still failing (' + persisting.length + ')';
      container.append(label, violationGroupList(persisting, 'bad'));
    }

    if (introduced.length) {
      const label = document.createElement('p');
      label.className = 'violation-group-label violation-group-label-warn';
      label.textContent = 'New after remediation (' + introduced.length + ')';
      container.append(label, violationGroupList(introduced, 'warn'));
    }

    if (resolved.length) {
      const details = document.createElement('details');
      details.className = 'violation-collapse';
      const summaryToggle = document.createElement('summary');
      summaryToggle.textContent = 'Resolved (' + resolved.length + ')';
      details.append(summaryToggle, violationGroupList(resolved, 'ok'));
      container.appendChild(details);
    }
  }).catch(() => {
    container.innerHTML = '<p class="muted">Unavailable.</p>';
  });

  return container;
}

function mergeViolations(report) {
  if (!report) return [];
  const merged = new Map();
  ['ua1', 'wcag'].forEach((profile) => {
    (((report.profiles || {})[profile] || {}).violations || []).forEach((violation) => {
      const key = violation.clause_test || 'unknown';
      if (!merged.has(key)) {
        merged.set(key, { clause_test: key, description: violation.description || '',
                          profiles: [] });
      }
      const entry = merged.get(key);
      const label = profile.toUpperCase();
      if (!entry.profiles.includes(label)) entry.profiles.push(label);
      if (!entry.description) entry.description = violation.description || '';
    });
  });
  return Array.from(merged.values())
    .sort((a, b) => a.clause_test.localeCompare(b.clause_test));
}

/* ---------- wiring ---------- */

const drop = el('drop');
// The whole page is a drop target, not just the drop zone box: dragging over
// nested elements fires dragenter/dragleave pairs constantly, so a depth
// counter is needed to avoid the highlight flickering as the cursor crosses
// child element boundaries.
let dragDepth = 0;
document.addEventListener('dragenter', (event) => {
  if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
  event.preventDefault();
  dragDepth += 1;
  drop.classList.add('over');
});
document.addEventListener('dragover', (event) => {
  if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
  event.preventDefault();
});
document.addEventListener('dragleave', (event) => {
  if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) drop.classList.remove('over');
});
document.addEventListener('drop', (event) => {
  if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
  event.preventDefault();
  dragDepth = 0;
  drop.classList.remove('over');
  addFiles(event.dataTransfer.files);
});
el('file-input').addEventListener('change', (event) => {
  addFiles(event.target.files);
  event.target.value = '';
});
el('clear-staged').addEventListener('click', () => {
  if (state.submitting) return;
  state.staged = [];
  el('batch-announcer').textContent = '';
  renderStaged();
  updateSubmitState();
});
el('config-select').addEventListener('change', renderConfigDescription);
el('job-search').addEventListener('input', (event) => {
  state.jobSearch = event.target.value;
  renderJobs();
});
el('job-status-filter').addEventListener('change', (event) => {
  state.jobStatusFilter = event.target.value;
  renderJobs();
});
el('submit').addEventListener('click', submitJobs);
document.addEventListener('click', (event) => {
  const openMenu = document.querySelector('.download-menu[open]');
  if (openMenu && !openMenu.contains(event.target)) {
    openMenu.removeAttribute('open');
    state.openDownloadJobId = null;
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  const openMenu = document.querySelector('.download-menu[open]');
  if (!openMenu) return;
  const summary = openMenu.querySelector('summary');
  openMenu.removeAttribute('open');
  state.openDownloadJobId = null;
  summary.focus();
});

loadConfigs();
loadHealth();
refreshQueue();
startPolling();
