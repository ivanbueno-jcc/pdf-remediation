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
  jobRows: new Map(),
  cancellingJobs: new Set(),
  jobStatusSnapshot: null,
  jobSearch: '',
  jobOutcomeFilter: 'all',
  toastTimer: null,
  toastFadeTimer: null,
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
    chip.appendChild(statusIcon(check.required ? 'alert' : 'warning'));
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
    select.innerHTML = '';
    const groups = new Map();
    payload.files.forEach((entry) => {
      const groupName = entry.group || '';
      let optgroup = groups.has(groupName) ? groups.get(groupName) : undefined;
      if (optgroup === undefined) {
        optgroup = groupName ? document.createElement('optgroup') : null;
        if (optgroup) optgroup.label = groupName;
        groups.set(groupName, optgroup);
      }
      const option = document.createElement('option');
      option.value = entry.name;
      option.textContent = (entry.label || entry.name) +
        (entry.available ? '' : ' (unavailable)');
      option.dataset.description = entry.description || '';
      option.title = entry.description || '';
      option.disabled = !entry.available;
      option.selected = entry.name === payload.default;
      (optgroup || select).appendChild(option);
    });
    groups.forEach((optgroup) => { if (optgroup) select.appendChild(optgroup); });
    renderConfigDescription();
  } catch (error) {
    select.innerHTML = '<option value="default.json">Standard</option>';
    select.options[0].dataset.description =
      'Runs the full remediation preset for the broadest automatic accessibility repair coverage.';
    renderConfigDescription();
  }
}

/* ---------- staging ---------- */

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
      state.staged.push({ file, status: 'error', reason, motion: 'enter' });
      errors.push({ name: file.name, reason });
      return;
    }

    const item = { file, status: 'accepted', reason: 'Ready to upload.', motion: 'enter' };
    state.staged.push(item);
    ready.push(item);
    totalBytes += file.size;
  });
  announceSelectionErrors(errors);
  if (acceptedItems().length > previousCount) clearToast();
  renderStaged();
  updateSubmitState();
}

function batchStatusLabel(status) {
  return { accepted: 'Ready', error: 'Needs attention', uploading: 'Uploading',
           queued: 'Queued' }[status] || status;
}

function batchStatusIcon(status) {
  return { accepted: 'check', error: 'alert', uploading: 'upload',
           queued: 'clock' }[status] || 'info';
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
    if (item.motion) {
      const motionClass = item.motion === 'exit' ? 'stage-row-exit' : 'stage-row-enter';
      row.classList.add(motionClass);
      item.motion = '';
      if (motionClass === 'stage-row-enter') {
        row.addEventListener('animationend', () => row.classList.remove(motionClass), { once: true });
      }
    }
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
    label.append(statusIcon(batchStatusIcon(item.status)), batchStatusLabel(item.status));
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
  submit.classList.toggle('is-submitting', state.submitting);
  const label = state.submitting ? 'Uploading…'
    : (readyCount === 1 ? 'Remediate 1 job'
      : (readyCount > 1 ? 'Remediate ' + readyCount + ' jobs' : 'Remediate'));
  submit.replaceChildren(downloadIcon(state.submitting ? 'spinner' : 'remediate'), label);
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

function waitForStagedExit() {
  const rows = Array.from(document.querySelectorAll('#staged-body tr.stage-row-exit'));
  const reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!rows.length || reducedMotion) return Promise.resolve();
  return new Promise((resolve) => {
    let remaining = rows.length;
    const done = () => {
      remaining -= 1;
      if (remaining === 0) resolve();
    };
    rows.forEach((row) => row.addEventListener('animationend', done, { once: true }));
    window.setTimeout(resolve, 280);
  });
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
  clearToast();
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
    showToast(String(error.message || error), 'bad');
    return;
  }

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
    if (job) item.motion = 'exit';
  });
  // Processing has started; remove queued files from the staging area. Keep
  // rejected files visible so the user can fix and resubmit them.
  renderStaged();
  await waitForStagedExit();
  state.submitting = false;
  state.staged = state.staged.filter((item) => item.status === 'error');
  el('batch-announcer').textContent = '';
  renderStaged();
  const queuedMessage = (payload.jobs.length === 1 ? '1 job queued' : payload.jobs.length + ' jobs queued') +
    ' · ' + payload.concurrency +
    ' run at a time, ' + payload.your_limit + ' of yours at once.';
  updateSubmitState('');
  showToast(queuedMessage, 'ok');
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
  const activeJobIds = new Set(jobs.filter(canCancelJob).map((job) => job.job_id));
  state.cancellingJobs.forEach((jobId) => {
    if (!activeJobIds.has(jobId)) state.cancellingJobs.delete(jobId);
  });
  announceJobChanges(jobs);
  state.jobs = jobs;
  const jobsSection = el('jobs-section');
  const shouldShowJobs = state.jobs.length > 0;
  const isEntering = shouldShowJobs && jobsSection.classList.contains('hidden');
  jobsSection.classList.toggle('hidden', !shouldShowJobs);
  if (isEntering) {
    jobsSection.classList.remove('jobs-section-enter');
    void jobsSection.offsetWidth;
    jobsSection.classList.add('jobs-section-enter');
    jobsSection.addEventListener(
      'animationend', () => jobsSection.classList.remove('jobs-section-enter'), { once: true }
    );
  }
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

function clearToast() {
  if (state.toastTimer) window.clearTimeout(state.toastTimer);
  if (state.toastFadeTimer) window.clearTimeout(state.toastFadeTimer);
  state.toastTimer = null;
  state.toastFadeTimer = null;
  const region = el('toast-region');
  if (region) {
    region.replaceChildren();
    region.setAttribute('aria-live', 'polite');
  }
}

function showToast(message, tone = 'ok') {
  clearToast();
  const region = el('toast-region');
  if (!region) return;
  const toastTone = ['ok', 'warn', 'bad'].includes(tone) ? tone : 'ok';
  const toast = document.createElement('div');
  toast.className = 'toast ' + toastTone;
  toast.setAttribute('role', toastTone === 'bad' ? 'alert' : 'status');
  toast.textContent = message;
  region.setAttribute('aria-live', toastTone === 'bad' ? 'assertive' : 'polite');
  region.appendChild(toast);
  state.toastTimer = window.setTimeout(() => {
    const reducedMotion = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reducedMotion) {
      clearToast();
      return;
    }
    toast.classList.add('toast-fade-out');
    state.toastFadeTimer = window.setTimeout(clearToast, 280);
  }, 4200);
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

function activeJobOrder(a, b) {
  // Jobs already being processed occupy the next available slots, so keep
  // them above queued work. Among queued jobs, the runner's position is the
  // source of truth for processing order.
  const statusOrder = { running: 0, queued: 1 };
  const statusDifference = statusOrder[a.status] - statusOrder[b.status];
  if (statusDifference) return statusDifference;

  if (a.status === 'queued') {
    const aAhead = a.jobs_ahead !== null && a.jobs_ahead !== undefined &&
      Number.isFinite(Number(a.jobs_ahead)) ? Number(a.jobs_ahead) : Number.MAX_SAFE_INTEGER;
    const bAhead = b.jobs_ahead !== null && b.jobs_ahead !== undefined &&
      Number.isFinite(Number(b.jobs_ahead)) ? Number(b.jobs_ahead) : Number.MAX_SAFE_INTEGER;
    if (aAhead !== bAhead) return aAhead - bAhead;
  }

  const aCreated = Date.parse(a.created_at || '') || 0;
  const bCreated = Date.parse(b.created_at || '') || 0;
  return aCreated - bCreated;
}

function filteredJobs() {
  const query = state.jobSearch.trim().toLowerCase();
  const filter = state.jobOutcomeFilter;
  return state.jobs.filter((job) => {
    if (query && !String(job.name || '').toLowerCase().includes(query)) return false;
    if (filter === 'pending' && job.outcome) return false;
    if (filter !== 'all' && filter !== 'pending' && job.outcome !== filter) return false;
    return true;
  });
}

function formatJobTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return month + '/' + day + '/' + date.getFullYear();
}

function formatPageCount(value) {
  if (value === null || value === undefined) return '';
  const count = Number(value);
  if (!Number.isInteger(count) || count < 0) return '';
  return count + (count === 1 ? ' page' : ' pages');
}

function renderJobGroup(groupId, bodyId, countId, jobs) {
  el(groupId).classList.toggle('hidden', jobs.length === 0);
  el(countId).textContent = jobs.length ? '(' + jobs.length + ')' : '';
  renderJobRows(el(bodyId), jobs);
}

function hasPassedProfile(job, profile) {
  return Boolean(job.after && job.after.profiles && job.after.profiles[profile] &&
    job.after.profiles[profile].passed);
}

function renderJobStats() {
  const jobs = state.jobs;
  const processed = jobs.filter((job) => job.status === 'completed');
  const pageCount = (job) => {
    const count = Number(job.page_count);
    return Number.isInteger(count) && count >= 0 ? count : null;
  };
  const totalPages = jobs.reduce((total, job) => total + (pageCount(job) || 0), 0);
  const processedPages = processed.reduce(
    (total, job) => total + (pageCount(job) || 0), 0
  );
  const stats = [
    ['files', 'Files processed', processed.length],
    ['check', 'WCAG compliant', processed.filter((job) => hasPassedProfile(job, 'wcag')).length],
    ['check', 'UA1 compliant', processed.filter((job) => hasPassedProfile(job, 'ua1')).length],
    ['pages', 'Pages remediated', processedPages + ' / ' + totalPages],
  ];
  const container = el('jobs-stats');
  container.replaceChildren(...stats.map(([iconName, label, value]) => {
    const stat = document.createElement('div');
    stat.className = 'job-stat';
    stat.setAttribute('aria-label', value + ' ' + label);
    const icon = statusIcon(iconName);
    icon.classList.add('job-stat-icon');
    const copy = document.createElement('div');
    copy.className = 'job-stat-copy';
    const number = document.createElement('strong');
    number.className = 'job-stat-value';
    number.textContent = value;
    const name = document.createElement('span');
    name.className = 'job-stat-label';
    name.textContent = label;
    copy.append(number, name);
    stat.append(icon, copy);
    return stat;
  }));
}

function renderJobs() {
  const visible = filteredJobs();
  const active = visible.filter(isActiveJob).sort(activeJobOrder);
  const recent = visible.filter((job) => !isActiveJob(job));
  const total = state.jobs.length;
  const empty = el('job-empty');
  empty.classList.toggle('hidden', visible.length > 0);
  empty.textContent = total
    ? 'No jobs match the current search and outcome filter.'
    : 'No jobs yet.';
  renderJobStats();
  renderJobGroup('active-jobs-group', 'active-jobs-body', 'active-jobs-count', active);
  renderJobGroup('recent-jobs-group', 'recent-jobs-body', 'recent-jobs-count', recent);

  // Drop rows for jobs that no longer exist or fell out of the current
  // search/outcome filter, so stale elements don't linger detached in memory.
  const visibleIds = new Set(visible.map((job) => job.job_id));
  state.jobRows.forEach((entry, jobId) => {
    if (visibleIds.has(jobId)) return;
    entry.row.remove();
    entry.detail.remove();
    state.jobRows.delete(jobId);
  });
}

function animateJobRemoval(jobIds) {
  const entries = jobIds
    .map((jobId) => state.jobRows.get(jobId))
    .filter(Boolean);
  if (!entries.length) return Promise.resolve();
  entries.forEach((entry) => {
    entry.row.classList.add('job-row-exit');
    entry.detail.classList.add('job-row-exit');
  });
  const reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reducedMotion) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, 230));
}

function buildJobRow(job) {
  const row = document.createElement('tr');
  row.className = 'job-row job-row-enter';
  row.dataset.jobId = job.job_id;
  row.addEventListener('animationend', () => row.classList.remove('job-row-enter'), { once: true });

  const name = document.createElement('td');
  name.className = 'name';
  const fileLabel = document.createElement('div');
  fileLabel.className = 'file-label';
  const disclosure = document.createElement('button');
  disclosure.type = 'button';
  disclosure.className = 'disclosure';
  disclosure.setAttribute('aria-expanded', 'false');
  disclosure.setAttribute('aria-label', 'Show details for ' + job.name);
  disclosure.title = 'Show details for ' + job.name;
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
  const metadata = [formatJobTimestamp(job.created_at), formatPageCount(job.page_count),
    job.config_label || job.config_file]
    .filter(Boolean);
  if (metadata.length) {
    const meta = document.createElement('div');
    meta.className = 'job-meta';
    meta.textContent = metadata.join(' · ');
    fileInfo.appendChild(meta);
  }
  const fileActions = document.createElement('div');
  fileActions.className = 'file-actions';
  fileInfo.appendChild(fileActions);
  fileLabel.append(disclosure, fileInfo);
  name.appendChild(fileLabel);

  const processingState = document.createElement('span');
  const status = document.createElement('td');
  status.className = 'job-status';
  const stateStack = document.createElement('div');
  stateStack.className = 'job-state-stack';
  const outcome = document.createElement('span');
  const progressLive = document.createElement('span');
  progressLive.className = 'sr-only job-progress-live';
  progressLive.setAttribute('role', 'status');
  progressLive.setAttribute('aria-live', 'polite');
  progressLive.setAttribute('aria-atomic', 'true');
  stateStack.append(processingState, outcome);
  stateStack.appendChild(progressLive);
  status.appendChild(stateStack);

  const validation = document.createElement('td');
  validation.className = 'validation-change';

  const downloads = document.createElement('td');
  downloads.className = 'actions';

  row.append(name, status, validation, downloads);

  const detail = document.createElement('tr');
  detail.className = 'detail-row hidden';
  detail.id = 'job-details-' + job.job_id;
  detail.setAttribute('aria-hidden', 'true');
  disclosure.setAttribute('aria-controls', detail.id);
  const cell = document.createElement('td');
  cell.colSpan = 4;
  detail.appendChild(cell);

  const entry = {
    job, row, detail, cell, disclosure,
    processingState, outcome, progressLive, status, validation, downloads, fileActions,
  };

  fileName.classList.add('file-name-toggle');
  fileName.addEventListener('click', () => {
    toggleJob(entry.job, entry.row, entry.detail, entry.cell, entry.disclosure);
  });

  // `entry.job` is refreshed on every poll via updateJobRow, so this closure
  // always acts on the latest data even though it's bound once at creation.
  disclosure.addEventListener('click', () => {
    toggleJob(entry.job, entry.row, entry.detail, entry.cell, entry.disclosure);
  });

  return entry;
}

function updateJobRow(entry, job) {
  const previousStatus = entry.row.dataset.status;
  const previousOutcome = entry.row.dataset.outcome;
  entry.job = job;
  entry.row.dataset.status = job.status;
  entry.row.dataset.outcome = job.outcome || '';

  const detailNote = processingDetail(job);
  const label = processingLabel(job.status);
  const progressMessage = isActiveJob(job)
    ? job.name + ': ' + label + (detailNote ? ', ' + detailNote : '') : '';
  entry.processingState.className = 'processing-state ' + job.status;
  entry.processingState.textContent = '';
  entry.processingState.append(statusIcon(processingStatusIcon(job.status)), detailNote || label);
  if (detailNote) entry.processingState.setAttribute('aria-label', label + ': ' + detailNote);
  else entry.processingState.removeAttribute('aria-label');
  if (entry.progressLive.textContent !== progressMessage) {
    entry.progressLive.textContent = progressMessage;
  }

  entry.outcome.className = 'outcome ' + (job.outcome ? outcomeTone(job.outcome) : 'pending');
  entry.outcome.textContent = '';
  entry.outcome.append(
    statusIcon(outcomeStatusIcon(job.outcome)),
    job.outcome_label || 'Pending result'
  );
  entry.status.querySelectorAll('.muted').forEach((note) => note.remove());
  if (job.error) {
    const note = document.createElement('div');
    note.className = 'muted';
    note.textContent = job.error;
    entry.status.appendChild(note);
  }

  const statusChanged = previousStatus !== undefined && previousStatus !== job.status;
  const outcomeChanged = previousOutcome !== undefined && previousOutcome !== (job.outcome || '');
  const reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reducedMotion && (statusChanged || outcomeChanged)) {
    [entry.processingState, entry.outcome].forEach((state) => {
      state.classList.remove('status-transition');
      void state.offsetWidth;
      state.classList.add('status-transition');
      state.addEventListener('animationend', () => state.classList.remove('status-transition'), { once: true });
    });
  }

  entry.fileActions.innerHTML = '';
  if (canCancelJob(job)) {
    const isCancelling = state.cancellingJobs.has(job.job_id);
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'cancel-action' + (isCancelling ? ' is-cancelling' : '');
    cancel.append(downloadIcon(isCancelling ? 'spinner' : 'cancel'),
      isCancelling ? 'Cancelling' : 'Cancel');
    cancel.disabled = isCancelling;
    if (isCancelling) cancel.setAttribute('aria-busy', 'true');
    cancel.setAttribute('aria-label', (isCancelling ? 'Cancelling ' : 'Cancel ') + job.name);
    cancel.addEventListener('click', async () => {
      if (!await confirmCancel(job)) return;
      await cancelJob(job, cancel);
    });
    entry.fileActions.appendChild(cancel);
  }
  if (canDeleteJob(job)) {
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'delete-action';
    remove.append(downloadIcon('delete'), 'Delete');
    remove.setAttribute('aria-label', 'Delete ' + job.name);
    remove.title = 'Delete ' + job.name;
    remove.addEventListener('click', () => deleteJob(job, remove));
    entry.fileActions.appendChild(remove);
  }
  if (canRetryJob(job)) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'retry-action';
    retry.append(downloadIcon('retry'), 'Retry');
    retry.setAttribute('aria-label', 'Stage processed PDF for ' + job.name);
    retry.title = 'Retry ' + job.name;
    retry.addEventListener('click', () => retryProcessedPdf(job, retry));
    entry.fileActions.appendChild(retry);
  }

  entry.validation.innerHTML = validationComparison(job.before, job.after);

  entry.downloads.innerHTML = '';
  const base = '/api/jobs/' + job.job_id + '/';
  const split = document.createElement('div');
  split.className = 'split-download';
  const primary = link(base + 'pdf', pdfDownloadLabel(job), job.has_pdf,
    job.name, 'file-check');
  primary.classList.add('split-primary');
  split.appendChild(primary);

  const menu = document.createElement('details');
  menu.className = 'download-menu';
  menu.dataset.jobId = job.job_id;
  menu.open = state.openDownloadJobId === job.job_id;
  const menuSummary = document.createElement('summary');
  menuSummary.setAttribute('aria-label', 'More download options for ' + job.name);
  menuSummary.title = 'More download options for ' + job.name;
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
  entry.downloads.appendChild(split);
}

function renderJobRows(body, jobs) {
  const initialTops = new Map();
  jobs.forEach((job) => {
    const existing = state.jobRows.get(job.job_id);
    if (existing && existing.row.parentElement === body &&
        !existing.row.classList.contains('job-row-enter')) {
      initialTops.set(job.job_id, existing.row.getBoundingClientRect().top);
    }
  });
  jobs.forEach((job, index) => {
    let entry = state.jobRows.get(job.job_id);
    if (!entry) {
      entry = buildJobRow(job);
      state.jobRows.set(job.job_id, entry);
    }
    entry.row.classList.toggle('job-row-stripe', index % 2 === 1);
    updateJobRow(entry, job);
    // appendChild on an already-attached node moves it (including across
    // tbodies, e.g. active -> recent when a job finishes) instead of
    // recreating it, which is what lets CSS transitions animate the change.
    body.appendChild(entry.row);
    body.appendChild(entry.detail);
    if (state.openJobId === job.job_id) {
      toggleJob(entry.job, entry.row, entry.detail, entry.cell, entry.disclosure, true);
    }
  });

  const reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reducedMotion || !initialTops.size) return;
  requestAnimationFrame(() => {
    jobs.forEach((job) => {
      const entry = state.jobRows.get(job.job_id);
      const initialTop = initialTops.get(job.job_id);
      if (!entry || initialTop === undefined) return;
      const delta = initialTop - entry.row.getBoundingClientRect().top;
      if (Math.abs(delta) < 1) return;
      entry.row.style.transition = 'none';
      entry.row.style.transform = 'translateY(' + delta + 'px)';
      void entry.row.offsetWidth;
      entry.row.style.transition = '';
      entry.row.style.transform = '';
    });
  });
}

function processingLabel(status) {
  return { queued: 'Queued', running: 'Running', completed: 'Complete',
           failed: 'Failed', cancelled: 'Cancelled' }[status] || status;
}

function processingStatusIcon(status) {
  return { queued: 'clock', running: 'activity', completed: 'check',
           failed: 'failed', cancelled: 'cancelled' }[status] || 'info';
}

function pipelineStageLabel(stage) {
  const labels = {
    validate_before: 'Initial validation',
    compliance_gate: 'Compliance check',
    unlock: 'Remove security',
    fix: 'Apply remediation',
    font_fix: 'Font repair',
    font_fix_callas: 'Callas font repair',
    font_fix_pdfix: 'PDFix font repair',
    fix_target: 'Targeted repairs',
    validate_after: 'Final validation',
  };
  if (labels[stage]) return labels[stage];
  const words = String(stage || '').replaceAll('_', ' ');
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Unknown stage';
}

function processingDetail(job) {
  if (job.status === 'queued') {
    if (job.jobs_ahead === 0 || job.jobs_ahead === null) return 'Next to run';
    return job.jobs_ahead + ' ahead';
  }
  if (job.status === 'running' && job.current_stage) {
    return pipelineStageLabel(job.current_stage);
  }
  return '';
}

function outcomeTone(outcome) {
  if (outcome === 'remediated' || outcome === 'already_compliant') return 'ok';
  if (outcome === 'improved' || outcome === 'unchanged' || outcome === 'cancelled') return 'warn';
  return 'bad';
}

function outcomeStatusIcon(outcome) {
  return { remediated: 'check', already_compliant: 'check', improved: 'improved',
           unchanged: 'unchanged', cancelled: 'cancelled', failed: 'failed' }[outcome] || 'clock';
}

function canRetryJob(job) {
  return Boolean(job && job.has_pdf && job.status !== 'queued' && job.status !== 'running' &&
    job.outcome && job.outcome !== 'remediated' && job.outcome !== 'already_compliant');
}

function canCancelJob(job) {
  return Boolean(job && (job.status === 'queued' || job.status === 'running'));
}

function canDeleteJob(job) {
  return Boolean(job && job.status !== 'queued' && job.status !== 'running');
}

function pdfDownloadLabel(job) {
  return job.outcome === 'remediated' ? 'Remediated PDF' : 'Processed PDF';
}

async function retryProcessedPdf(job, button) {
  if (button.disabled || state.submitting) return;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch('/api/jobs/' + encodeURIComponent(job.job_id) + '/pdf');
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(describeError(payload) || 'The processed PDF is unavailable.');
    }
    const blob = await response.blob();
    const file = new File([blob], job.name, {
      type: blob.type || 'application/pdf', lastModified: Date.now(),
    });
    const beforeCount = acceptedItems().length;
    addFiles([file]);
    if (acceptedItems().length > beforeCount) {
      const message = job.name + ' processed PDF added to staging.';
      showToast(message);
      announceStatus(message);
      const reducedMotion = window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      el('submit-section').scrollIntoView({
        behavior: reducedMotion ? 'auto' : 'smooth', block: 'start',
      });
    }
  } catch (error) {
    showToast('Could not stage the processed PDF: ' + String(error.message || error), 'bad');
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}

async function cancelJob(job, button) {
  if (button.disabled || !canCancelJob(job)) return;
  state.cancellingJobs.add(job.job_id);
  button.classList.add('is-cancelling');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.setAttribute('aria-label', 'Cancelling ' + job.name);
  button.replaceChildren(downloadIcon('spinner'), 'Cancelling');
  try {
    const response = await fetch('/api/jobs/' + encodeURIComponent(job.job_id) + '/cancel', {
      method: 'POST',
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(describeError(payload) || 'The job could not be cancelled.');
    }
    await refreshQueue();
    announceStatus('Cancellation requested for ' + job.name + '.');
  } catch (error) {
    state.cancellingJobs.delete(job.job_id);
    button.classList.remove('is-cancelling');
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.setAttribute('aria-label', 'Cancel ' + job.name);
    button.replaceChildren(downloadIcon('cancel'), 'Cancel');
    showToast('Could not cancel the job: ' + String(error.message || error), 'bad');
  }
}

async function deleteJob(job, button) {
  if (button.disabled || state.submitting) return;
  if (!await confirmDelete(job)) return;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch('/api/jobs/' + encodeURIComponent(job.job_id), {
      method: 'DELETE',
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(describeError(payload) || 'The job could not be deleted.');
    }
    await animateJobRemoval([job.job_id]);
    await refreshQueue();
    const message = job.name + ' and its artifacts were deleted.';
    showToast(message);
    announceStatus(message);
  } catch (error) {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    showToast('Could not delete the job: ' + String(error.message || error), 'bad');
  }
}

function confirmDelete(job) {
  return confirmAction(
    'Delete ' + job.name + '?',
    'This permanently removes the job and all of its artifacts. This action cannot be undone.',
    'Delete job'
  );
}

function confirmDeleteAll(count) {
  const noun = count === 1 ? 'job' : 'jobs';
  return confirmAction(
    'Delete all jobs?',
    'This permanently removes all ' + count + ' ' + noun + ' and their artifacts. Jobs still running will be skipped until they finish. This action cannot be undone.',
    'Delete all'
  );
}

function confirmCancel(job) {
  const copy = job.status === 'running'
    ? 'Processing will stop at the next safe point. Incomplete results will not be available.'
    : 'This removes the job from the queue before processing begins.';
  return confirmAction('Cancel ' + job.name + '?', copy, 'Cancel job');
}

function confirmAction(titleText, copyText, confirmText) {
  const dialog = el('delete-dialog');
  const title = el('delete-dialog-title');
  const copy = el('delete-dialog-copy');
  const confirm = el('delete-confirm');
  const cancel = el('delete-cancel');
  title.textContent = titleText;
  copy.textContent = copyText;
  const previousConfirmLabel = confirm.textContent;
  confirm.textContent = confirmText;

  return new Promise((resolve) => {
    const previouslyFocused = document.activeElement;
    const onClose = () => {
      confirm.textContent = previousConfirmLabel;
      if (previouslyFocused && previouslyFocused.isConnected && previouslyFocused.focus) {
        previouslyFocused.focus();
      }
      resolve(dialog.returnValue === 'confirm');
    };
    dialog.addEventListener('close', onClose, { once: true });
    confirm.onclick = () => { dialog.returnValue = 'confirm'; dialog.close(); };
    cancel.onclick = () => { dialog.returnValue = 'cancel'; dialog.close(); };
    dialog.returnValue = '';
    dialog.showModal();
    cancel.focus();
  });
}

async function deleteAllJobs(button) {
  if (button.disabled || state.submitting || !state.jobs.length) return;
  if (!await confirmDeleteAll(state.jobs.length)) return;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    const jobs = state.jobs.slice();
    const response = await fetch('/api/jobs', { method: 'DELETE' });
    let payload = await response.json().catch(() => ({}));
    if (response.status === 405) {
      // Older server processes may still have the per-job route but not the
      // bulk route. Keep the action usable while that process is being
      // restarted or reloaded.
      const results = await Promise.all(jobs.map(async (job) => {
        const itemResponse = await fetch('/api/jobs/' + encodeURIComponent(job.job_id), {
          method: 'DELETE',
        });
        return { job, response: itemResponse };
      }));
      const unexpected = results.find((item) => !item.response.ok && item.response.status !== 409);
      if (unexpected) {
        const itemPayload = await unexpected.response.json().catch(() => ({}));
        throw new Error(describeError(itemPayload) || 'The jobs could not be deleted.');
      }
      payload = {
        deleted: results.filter((item) => item.response.ok).map((item) => item.job.job_id),
        skipped: results.filter((item) => item.response.status === 409).map((item) => item.job.job_id),
      };
    } else if (!response.ok) {
      throw new Error(describeError(payload) || 'The jobs could not be deleted.');
    }
    const deleted = Array.isArray(payload.deleted) ? payload.deleted : [];
    const skipped = Array.isArray(payload.skipped) ? payload.skipped : [];
    await animateJobRemoval(deleted);
    await refreshQueue();
    if (skipped.length) {
      const message = deleted.length
        ? deleted.length + ' job' + (deleted.length === 1 ? '' : 's') +
          ' deleted. ' + skipped.length + ' active job' + (skipped.length === 1 ? '' : 's') + ' remain.'
        : 'Active jobs are still running and could not be deleted.';
      showToast(message, 'warn');
      announceStatus(message);
    } else {
      const message = deleted.length + ' job' + (deleted.length === 1 ? '' : 's') +
        ' and their artifacts were deleted.';
      showToast(message);
      announceStatus(message);
    }
  } catch (error) {
    showToast('Could not delete all jobs: ' + String(error.message || error), 'bad');
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
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

function statusIcon(name) {
  const namespace = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(namespace, 'svg');
  svg.classList.add('status-icon');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');

  const shapes = {
    files: [
      ['path', { d: 'M6 3h9l3 3v15H6z' }],
      ['path', { d: 'M15 3v4h4' }],
      ['path', { d: 'm9 14 2 2 4-4' }],
    ],
    check: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'm8 12 2.5 2.5L16 9' }],
    ],
    alert: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'M12 7v6' }],
      ['path', { d: 'M12 17h.01' }],
    ],
    warning: [
      ['path', { d: 'M10.3 4.1 2.7 17.4A1.8 1.8 0 0 0 4.3 20h15.4a1.8 1.8 0 0 0 1.6-2.6L13.7 4.1a2 2 0 0 0-3.4 0Z' }],
      ['path', { d: 'M12 9v4' }],
      ['path', { d: 'M12 17h.01' }],
    ],
    upload: [
      ['path', { d: 'M12 16V4' }],
      ['path', { d: 'm7 9 5-5 5 5' }],
      ['path', { d: 'M5 20h14' }],
    ],
    clock: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'M12 7v5l3 2' }],
    ],
    activity: [
      ['path', { d: 'M3 12h4l2.2-5 4.2 10 2.2-5H21' }],
    ],
    failed: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'm9 9 6 6M15 9l-6 6' }],
    ],
    cancelled: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'm6 6 12 12' }],
    ],
    improved: [
      ['path', { d: 'M4 17 10 11l4 4 6-7' }],
      ['path', { d: 'M15 8h5v5' }],
    ],
    unchanged: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'M8 12h8' }],
    ],
    info: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'M12 11v6' }],
      ['path', { d: 'M12 7h.01' }],
    ],
    pages: [
      ['path', { d: 'M5 4h9l3 3v13H5z' }],
      ['path', { d: 'M8 2h9l3 3v13h-2' }],
    ],
  };

  (shapes[name] || shapes.info).forEach(([tag, attributes]) => {
    const shape = document.createElementNS(namespace, tag);
    Object.entries(attributes).forEach(([key, value]) => shape.setAttribute(key, value));
    svg.appendChild(shape);
  });
  return svg;
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
    retry: [
      ['path', { d: 'M3 12a9 9 0 1 0 3-6.7' }],
      ['polyline', { points: '3 4 3 10 9 10' }],
    ],
    delete: [
      ['path', { d: 'M4 7h16' }],
      ['path', { d: 'M10 11v6M14 11v6' }],
      ['path', { d: 'M6 7l1 14h10l1-14' }],
      ['path', { d: 'M9 7V4h6v3' }],
    ],
    cancel: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'm9 9 6 6M15 9l-6 6' }],
    ],
    spinner: [
      ['path', { d: 'M21 12a9 9 0 1 1-6.2-8.6' }],
      ['path', { d: 'M21 3v6h-6' }],
    ],
    remediate: [
      ['path', { d: 'm12 3-1.5 4.5L6 9l4.5 1.5L12 15l1.5-4.5L18 9l-4.5-1.5L12 3z' }],
      ['path', { d: 'm19 14-.7 2.3L16 17l2.3.7L19 20l.7-2.3L22 17l-2.3-.7L19 14z' }],
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
  anchor.title = label + (enabled ? '' : ' (unavailable)');
  if (enabled) {
    anchor.href = href;
    anchor.addEventListener('click', () => {
      showToast(label + ' download started for ' + fileName + '.');
    });
  } else {
    anchor.setAttribute('aria-disabled', 'true');
    anchor.title = 'Unavailable until this artifact is ready';
  }
  return anchor;
}

/* ---------- job detail ---------- */

function updateDisclosure(disclosure, fileName, expanded) {
  const action = (expanded ? 'Hide' : 'Show') + ' details for ' + fileName;
  disclosure.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  disclosure.setAttribute('aria-label', action);
  disclosure.title = action;
}

function updateDetailHeight(detailRow) {
  const detail = detailRow.querySelector('.job-detail');
  if (detail) detailRow.style.setProperty('--detail-height', detail.scrollHeight + 'px');
}

function setDetailExpanded(detailRow, expanded) {
  const token = (Number(detailRow.dataset.animationToken) || 0) + 1;
  detailRow.dataset.animationToken = String(token);
  if (expanded) {
    detailRow.classList.remove('hidden');
    if (detailRow.classList.contains('detail-expanded')) return;
    requestAnimationFrame(() => {
      if (detailRow.dataset.animationToken === String(token) &&
          !detailRow.classList.contains('hidden')) {
        detailRow.classList.add('detail-expanded');
      }
    });
    return;
  }

  detailRow.classList.remove('detail-expanded');
  const finish = () => {
    if (detailRow.dataset.animationToken === String(token) &&
        !detailRow.classList.contains('detail-expanded')) {
      detailRow.classList.add('hidden');
    }
  };
  const reducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reducedMotion) finish();
  else window.setTimeout(finish, 290);
}

async function toggleJob(job, row, detailRow, cell, disclosure, forceOpen) {
  const isOpen = row.dataset.open === 'true';
  if (isOpen && !forceOpen) {
    row.dataset.open = 'false';
    setDetailExpanded(detailRow, false);
    detailRow.setAttribute('aria-hidden', 'true');
    updateDisclosure(disclosure, job.name, false);
    state.openJobId = null;
    return;
  }

  state.openJobId = job.job_id;
  row.dataset.open = 'true';
  detailRow.setAttribute('aria-hidden', 'false');
  updateDisclosure(disclosure, job.name, true);
  cell.innerHTML = '<div class="job-detail muted" role="status">Loading job details…</div>';
  updateDetailHeight(detailRow);
  setDetailExpanded(detailRow, true);

  try {
    const response = await fetch('/api/jobs/' + job.job_id);
    if (!response.ok) throw new Error('Details request failed.');
    const detailJob = await response.json();
    renderDetail(cell, detailJob);
    if (row.dataset.open === 'true') announceStatus('Details loaded for ' + job.name + '.');
  } catch (error) {
    cell.innerHTML = '<div class="job-detail" role="alert">Job details are unavailable.</div>';
    updateDetailHeight(detailRow);
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
    label.textContent = pipelineStageLabel(stage.name);
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

  cell.appendChild(wrap);
  updateDetailHeight(cell.parentElement);
}

function violationItem(violation) {
  const item = document.createElement('tr');
  const code = document.createElement('code');
  code.textContent = violation.clause_test;
  const codeCell = document.createElement('td');
  codeCell.className = 'violation-code';
  codeCell.appendChild(code);
  item.appendChild(codeCell);
  const profiles = document.createElement('td');
  profiles.className = 'violation-profiles';
  violation.profiles.forEach((profile) => {
    const tag = document.createElement('span');
    tag.className = 'profile-chip';
    tag.textContent = profile;
    profiles.appendChild(tag);
  });
  item.appendChild(profiles);
  const description = document.createElement('td');
  description.className = 'violation-description';
  description.textContent = violation.description || '';
  item.appendChild(description);
  return item;
}

function violationGroupList(violations, tone) {
  const table = document.createElement('table');
  table.className = 'violation-group' + (tone ? ' violation-group-' + tone : '');
  const body = document.createElement('tbody');
  violations.forEach((violation) => body.appendChild(violationItem(violation)));
  table.appendChild(body);
  return table;
}

function violationList(jobId, stage) {
  const list = violationGroupList([], '');
  const body = list.querySelector('tbody');
  const loading = document.createElement('tr');
  const loadingCell = document.createElement('td');
  loadingCell.colSpan = 3;
  loadingCell.className = 'muted';
  loadingCell.textContent = 'Loading…';
  loading.appendChild(loadingCell);
  body.appendChild(loading);
  fetch('/api/jobs/' + jobId + '/' + stage)
    .then((response) => (response.ok ? response.json() : null))
    .then((report) => {
      body.innerHTML = '';
      const merged = mergeViolations(report);
      if (!merged.length) {
        const none = document.createElement('tr');
        const noneCell = document.createElement('td');
        noneCell.colSpan = 3;
        noneCell.className = 'none';
        noneCell.textContent = 'No violations reported.';
        none.appendChild(noneCell);
        body.appendChild(none);
        return;
      }
      merged.forEach((violation) => body.appendChild(violationItem(violation)));
    })
    .catch(() => {
      body.innerHTML = '';
      const unavailable = document.createElement('tr');
      const unavailableCell = document.createElement('td');
      unavailableCell.colSpan = 3;
      unavailableCell.className = 'muted';
      unavailableCell.textContent = 'Unavailable.';
      unavailable.appendChild(unavailableCell);
      body.appendChild(unavailable);
    });
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
el('job-outcome-filter').addEventListener('change', (event) => {
  state.jobOutcomeFilter = event.target.value;
  renderJobs();
});
const deleteAllButton = el('delete-all');
deleteAllButton.prepend(downloadIcon('delete'));
deleteAllButton.addEventListener('click', () => deleteAllJobs(deleteAllButton));
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
