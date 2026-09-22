/* Pure job-detail projections, separate from detail DOM construction. */
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
  global.PdfWebJobDetail = Object.freeze({ validationValue, validationRequirementLabel, mergeViolations });
})(window);
