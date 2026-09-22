/* Queue filters and ETA math, independent from DOM rendering. */
(function installPdfWebQueueView(global) {
  'use strict';
  function isActive(job) { return job.status === 'queued' || job.status === 'running'; }
  function retainWindow(jobs, maxTerminal) {
    const active = jobs.filter(isActive);
    const terminal = jobs.filter((job) => !isActive(job))
      .sort((left, right) => right.created_at.localeCompare(left.created_at))
      .slice(0, maxTerminal);
    return active.concat(terminal).sort((left, right) =>
      right.created_at.localeCompare(left.created_at));
  }
  function etaSeconds(payload) {
    const jobs = payload.jobs || [];
    const active = jobs.filter(isActive);
    if (!active.length) return null;
    const durations = jobs.map((job) => {
      if (job.status !== 'completed') return null;
      const started = Date.parse(job.started_at || ''), finished = Date.parse(job.finished_at || '');
      return !started || !finished || finished <= started ? null : (finished - started) / 1000;
    }).filter((value) => value !== null && Number.isFinite(value) && value > 0);
    const average = durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : 60;
    const configured = [Number(payload.concurrency), Number(payload.your_limit)]
      .filter((value) => Number.isFinite(value) && value > 0);
    const slots = configured.length ? Math.max(1, Math.min(...configured)) : 1;
    const running = active.filter((job) => job.status === 'running');
    const queuedTime = Math.ceil((active.length - running.length) / Math.max(1, slots - running.length)) * average;
    const remaining = running.map((job) => {
      const started = Date.parse(job.started_at || '');
      return Math.max(5, average - (started ? Math.max(0, (Date.now() - started) / 1000) : 0));
    });
    return Math.max(remaining.length ? Math.max(...remaining) : 0, queuedTime);
  }
  function buildJobRow(job, helpers) {
    const document = global.document;
    const row = document.createElement('tr');
    row.className = 'job-row job-row-enter';
    row.dataset.jobId = job.job_id;
    row.addEventListener('animationend', () => row.classList.remove('job-row-enter'), { once: true });
    const name = document.createElement('td'); name.className = 'name';
    const fileLabel = document.createElement('div'); fileLabel.className = 'file-label';
    const disclosure = document.createElement('button');
    disclosure.type = 'button'; disclosure.className = 'disclosure';
    disclosure.setAttribute('aria-expanded', 'false');
    disclosure.setAttribute('aria-label', 'Show details for ' + job.name);
    disclosure.title = 'Show details for ' + job.name;
    const caret = document.createElement('span'); caret.className = 'caret';
    caret.setAttribute('aria-hidden', 'true'); caret.textContent = '›';
    disclosure.appendChild(caret);
    const fileInfo = document.createElement('div'); fileInfo.className = 'file-info';
    const fileName = document.createElement('a'); fileName.className = 'file-name';
    fileName.href = '/api/jobs/' + encodeURIComponent(job.job_id) + '/original';
    fileName.target = '_blank'; fileName.rel = 'noopener noreferrer';
    fileName.title = 'Open original PDF in a new tab'; fileName.textContent = job.name;
    fileInfo.appendChild(fileName);
    const meta = document.createElement('div'); meta.className = 'job-meta';
    helpers.renderJobMeta(meta, job); fileInfo.append(meta);
    const fileActions = document.createElement('div'); fileActions.className = 'file-actions';
    fileInfo.append(fileActions); fileLabel.append(disclosure, fileInfo); name.append(fileLabel);

    const processingState = document.createElement('span');
    const status = document.createElement('td'); status.className = 'job-status';
    const stateStack = document.createElement('div'); stateStack.className = 'job-state-stack';
    const outcomeWrap = document.createElement('span'); outcomeWrap.className = 'outcome-composite pending';
    const outcome = document.createElement('span');
    const validationRequirement = document.createElement('span'); validationRequirement.className = 'validation-requirement';
    const progressLive = document.createElement('span'); progressLive.className = 'sr-only job-progress-live';
    progressLive.setAttribute('role', 'status'); progressLive.setAttribute('aria-live', 'polite');
    progressLive.setAttribute('aria-atomic', 'true'); outcomeWrap.append(outcome, validationRequirement);
    stateStack.append(outcomeWrap, processingState, progressLive); status.append(stateStack);
    const validation = document.createElement('td'); validation.className = 'validation-change';
    const downloads = document.createElement('td'); downloads.className = 'actions';
    row.append(name, status, validation, downloads);
    const detail = document.createElement('tr'); detail.className = 'detail-row hidden';
    detail.id = 'job-details-' + job.job_id; detail.setAttribute('aria-hidden', 'true');
    disclosure.setAttribute('aria-controls', detail.id);
    const cell = document.createElement('td'); cell.colSpan = 4; detail.append(cell);
    const entry = { job, row, detail, cell, disclosure, processingState, outcome,
      outcomeWrap, validationRequirement, progressLive, status, validation, downloads,
      fileActions, meta };
    disclosure.addEventListener('click', () => helpers.toggleJob(
      entry.job, entry.row, entry.detail, entry.cell, entry.disclosure
    ));
    row.addEventListener('click', (event) => {
      if (helpers.shouldToggleJobRow(event.target)) helpers.toggleJob(
        entry.job, entry.row, entry.detail, entry.cell, entry.disclosure
      );
    });
    return entry;
  }
  function renderRows(body, jobs, helpers) {
    const state = helpers.state;
    const initialTops = new Map(), expectedNodes = [];
    jobs.forEach((job) => {
      const entry = state.jobRows.get(job.job_id);
      if (entry && entry.row.parentElement === body && !entry.row.classList.contains('job-row-enter')) {
        initialTops.set(job.job_id, entry.row.getBoundingClientRect().top);
      }
    });
    jobs.forEach((job, index) => {
      let entry = state.jobRows.get(job.job_id);
      if (!entry) { entry = helpers.buildJobRow(job); state.jobRows.set(job.job_id, entry); }
      entry.row.classList.toggle('job-row-stripe', index % 2 === 1);
      helpers.updateJobRow(entry, job);
      expectedNodes.push(entry.row, entry.detail);
      if (state.openJobId === job.job_id && entry.row.dataset.open !== 'true') {
        helpers.toggleJob(entry.job, entry.row, entry.detail, entry.cell, entry.disclosure, true);
      }
    });
    const orderChanged = body.children.length !== expectedNodes.length ||
      expectedNodes.some((node, index) => body.children[index] !== node);
    if (orderChanged) expectedNodes.forEach((node) => body.appendChild(node));
    const reduced = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || !initialTops.size) return;
    global.requestAnimationFrame(() => jobs.forEach((job) => {
      const entry = state.jobRows.get(job.job_id), initialTop = initialTops.get(job.job_id);
      if (!entry || initialTop === undefined) return;
      const delta = initialTop - entry.row.getBoundingClientRect().top;
      if (Math.abs(delta) < 1) return;
      entry.row.style.transition = 'none'; entry.row.style.transform = 'translateY(' + delta + 'px)';
      void entry.row.offsetWidth; entry.row.style.transition = ''; entry.row.style.transform = '';
    }));
  }
  global.PdfWebQueueView = Object.freeze({ isActive, retainWindow, etaSeconds, buildJobRow, renderRows });
})(window);
