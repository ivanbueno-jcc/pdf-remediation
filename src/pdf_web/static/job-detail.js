/* Job-detail projections and content renderer, separate from app bootstrap. */
(function installPdfWebJobDetail(global) {
  'use strict';
  function validationValue(entry) {
    if (!entry) return { text: 'Pending', tone: 'pending' };
    if (entry.status === 'pass') return { text: 'Pass', tone: 'ok' };
    if (entry.status === 'error') return { text: 'Error', tone: 'warn' };
    const count = Number(entry.failed_rules_count || 0);
    return { text: count + ' fail' + (count === 1 ? '' : 's'), tone: 'bad' };
  }
  function validationRequirementLabel(requirement) {
    return ({
      'wcag only': 'WCAG', 'pdfua1 only': 'PDF/UA-1',
      'wcag and pdfua1': 'WCAG • PDF/UA-1',
    })[requirement] || 'WCAG';
  }
  function mergeViolations(report) {
    if (!report) return [];
    const merged = new Map();
    ['ua1', 'wcag'].forEach((profile) => {
      (((report.profiles || {})[profile] || {}).violations || []).forEach((violation) => {
        const key = violation.clause_test || 'unknown';
        if (!merged.has(key)) merged.set(key, { clause_test: key, description: violation.description || '', profiles: [] });
        const item = merged.get(key), label = profile.toUpperCase();
        if (!item.profiles.includes(label)) item.profiles.push(label);
        if (!item.description) item.description = violation.description || '';
      });
    });
    return Array.from(merged.values()).sort((a, b) => a.clause_test.localeCompare(b.clause_test));
  }
  function render(cell, job, helpers) {
    cell.innerHTML = '';
    const wrap = global.document.createElement('div');
    wrap.className = 'job-detail';
    const layout = global.document.createElement('div');
    layout.className = 'job-detail-layout';
    const sidebar = global.document.createElement('aside');
    sidebar.className = 'job-detail-sidebar';
    const heading = global.document.createElement('h4');
    heading.id = 'pipeline-stages-' + job.job_id;
    heading.textContent = 'Pipeline stages';
    sidebar.setAttribute('aria-labelledby', heading.id);
    sidebar.appendChild(heading);
    const list = global.document.createElement('ol');
    list.className = 'stages';
    (job.stages || []).forEach((stage) => {
      const item = global.document.createElement('li');
      item.dataset.status = stage.status;
      const marker = global.document.createElement('span');
      marker.className = 'marker';
      marker.textContent = stage.status === 'ok' ? '✓' : (stage.status === 'failed' ? '✕' : '–');
      const label = global.document.createElement('span');
      label.textContent = helpers.pipelineStageLabel(stage.name);
      const detail = global.document.createElement('span');
      detail.className = 'detail';
      detail.textContent = stage.detail || '';
      item.append(marker, label, detail);
      list.appendChild(item);
    });
    sidebar.appendChild(list);
    (job.warnings || []).forEach((warning) => {
      const note = global.document.createElement('p');
      note.className = 'muted';
      note.textContent = warning;
      sidebar.appendChild(note);
    });
    const violations = global.document.createElement('div');
    violations.className = 'job-detail-violations';
    violations.setAttribute('role', 'region');
    violations.setAttribute('aria-label', 'Accessibility violations');
    helpers.appendViolationSection(violations, job);
    layout.append(sidebar, violations);
    wrap.appendChild(layout);
    cell.appendChild(wrap);
    helpers.updateDetailHeight(cell.parentElement);
  }
  global.PdfWebJobDetail = Object.freeze({
    validationValue, validationRequirementLabel, mergeViolations, render,
  });
})(window);
