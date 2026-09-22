/* Job actions and their network/error handling, independent of row rendering. */
(function installPdfWebActions(global) {
  'use strict';
  function create(d) {
    const confirm = (title, copy, label) => global.PdfWebDialogs.confirmAction(title, copy, label);
    const confirmDelete = (job) => confirm('Delete ' + job.name + '?',
      'This permanently removes the file and all of its artifacts. This action cannot be undone.',
      'Delete file');
    const confirmDeleteAll = (count) => confirm('Delete all files?',
      'This permanently removes all ' + count + ' file' + (count === 1 ? '' : 's') +
      ' and their artifacts. Files still processing will be skipped until they finish. This action cannot be undone.',
      'Delete all');
    const confirmCancel = (job) => confirm('Cancel ' + job.name + '?',
      job.status === 'running'
        ? 'Processing will stop at the next safe point. Incomplete results will not be available.'
        : 'This removes the file from the queue before processing begins.', 'Cancel file');

    async function retryProcessedPdf(job, button) {
      if (button.disabled || d.state.submitting) return;
      button.disabled = true; button.setAttribute('aria-busy', 'true');
      try {
        const response = await d.apiRequest('/api/jobs/' + encodeURIComponent(job.job_id) + '/pdf');
        if (!response.ok) throw new Error(d.describeError(await response.json().catch(() => ({}))) || 'The processed PDF is unavailable.');
        const blob = await response.blob();
        const file = new global.File([blob], job.name, { type: blob.type || 'application/pdf', lastModified: Date.now() });
        const beforeCount = d.acceptedItems().length;
        d.addFiles([file]);
        if (d.acceptedItems().length > beforeCount) {
          const message = job.name + ' processed PDF added to staging.';
          d.showToast(message); d.announceStatus(message);
          const reduced = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
          d.el('submit-section').scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
        }
      } catch (error) {
        d.showToast('Could not stage the processed PDF: ' + String(error.message || error), 'bad');
      } finally { button.disabled = false; button.removeAttribute('aria-busy'); }
    }

    async function cancelJob(job, button) {
      if (button.disabled || !d.canCancelJob(job)) return;
      d.state.cancellingJobs.add(job.job_id);
      button.classList.add('is-cancelling'); button.disabled = true;
      button.setAttribute('aria-busy', 'true'); button.setAttribute('aria-label', 'Cancelling ' + job.name);
      button.replaceChildren(d.downloadIcon('spinner'), 'Cancelling');
      try {
        const response = await d.apiRequest('/api/jobs/' + encodeURIComponent(job.job_id) + '/cancel', { method: 'POST' });
        if (!response.ok) throw new Error(d.describeError(await response.json().catch(() => ({}))) || 'The file could not be cancelled.');
        await d.loadQueueSnapshot(); d.announceStatus('Cancellation requested for ' + job.name + '.');
      } catch (error) {
        d.state.cancellingJobs.delete(job.job_id); button.classList.remove('is-cancelling');
        button.disabled = false; button.removeAttribute('aria-busy');
        button.setAttribute('aria-label', 'Cancel ' + job.name);
        button.replaceChildren(d.downloadIcon('cancel'), 'Cancel');
        d.showToast('Could not cancel the file: ' + String(error.message || error), 'bad');
      }
    }

    async function deleteJob(job, button) {
      if (button.disabled || d.state.submitting || !await confirmDelete(job)) return;
      button.disabled = true; button.setAttribute('aria-busy', 'true');
      try {
        const response = await d.apiRequest('/api/jobs/' + encodeURIComponent(job.job_id), { method: 'DELETE' });
        if (!response.ok) throw new Error(d.describeError(await response.json().catch(() => ({}))) || 'The file could not be deleted.');
        await d.animateJobRemoval([job.job_id]); await d.loadQueueSnapshot();
        const message = job.name + ' and its artifacts were deleted.';
        d.showToast(message); d.announceStatus(message);
      } catch (error) {
        button.disabled = false; button.removeAttribute('aria-busy');
        d.showToast('Could not delete the file: ' + String(error.message || error), 'bad');
      }
    }

    async function deleteAllJobs(button) {
      if (button.disabled || d.state.submitting || !d.state.jobs.length ||
          !await confirmDeleteAll(d.state.jobs.length)) return;
      button.disabled = true; button.setAttribute('aria-busy', 'true');
      try {
        const jobs = d.state.jobs.slice();
        const response = await d.apiRequest('/api/jobs', { method: 'DELETE' });
        let payload = await response.json().catch(() => ({}));
        if (response.status === 405) {
          const results = await Promise.all(jobs.map(async (job) => ({
            job,
            response: await d.apiRequest('/api/jobs/' + encodeURIComponent(job.job_id), { method: 'DELETE' }),
          })));
          const unexpected = results.find((item) => !item.response.ok && item.response.status !== 409);
          if (unexpected) throw new Error(d.describeError(await unexpected.response.json().catch(() => ({}))) || 'The files could not be deleted.');
          payload = {
            deleted: results.filter((item) => item.response.ok).map((item) => item.job.job_id),
            skipped: results.filter((item) => item.response.status === 409).map((item) => item.job.job_id),
          };
        } else if (!response.ok) throw new Error(d.describeError(payload) || 'The files could not be deleted.');
        const deleted = Array.isArray(payload.deleted) ? payload.deleted : [];
        const skipped = Array.isArray(payload.skipped) ? payload.skipped : [];
        await d.animateJobRemoval(deleted); await d.loadQueueSnapshot();
        const message = skipped.length
          ? (deleted.length ? deleted.length + ' file' + (deleted.length === 1 ? '' : 's') +
            ' deleted. ' + skipped.length + ' active file' + (skipped.length === 1 ? '' : 's') + ' remain.'
            : 'Active files are still processing and could not be deleted.')
          : deleted.length + ' file' + (deleted.length === 1 ? '' : 's') + ' and their artifacts were deleted.';
        d.showToast(message, skipped.length ? 'warn' : 'ok'); d.announceStatus(message);
      } catch (error) {
        d.showToast('Could not delete all files: ' + String(error.message || error), 'bad');
      } finally { button.disabled = false; button.removeAttribute('aria-busy'); }
    }

    return Object.freeze({ retryProcessedPdf, cancelJob, deleteJob, deleteAllJobs, confirmCancel });
  }
  global.PdfWebActions = Object.freeze({ create });
})(window);
