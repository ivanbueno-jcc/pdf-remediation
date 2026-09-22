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
  global.PdfWebQueueView = Object.freeze({ isActive, retainWindow, etaSeconds });
})(window);
