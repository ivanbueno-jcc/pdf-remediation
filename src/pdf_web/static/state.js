/* Shared application state, kept separate from rendering and event wiring. */
(function installPdfWebState(global) {
  'use strict';
  global.PdfWebState = {
    staged: [], submitting: false,
    uploadLimits: {
      max_files: 200, max_file_bytes: 200 * 1024 * 1024,
      max_submission_bytes: 2 * 1024 * 1024 * 1024,
    },
    health: null, authError: null, openJobId: null, openDownloadJobId: null,
    queueStream: null, jobs: [], jobIndex: new Map(), jobPositions: new Map(),
    queueMeta: {}, jobRows: new Map(), activeJobCount: 0, failedJobCount: 0,
    queueEta: null, queueEtaTimer: null, queueGeneration: null,
    jobStats: { processed: 0, wcag: 0, ua1: 0, totalPages: 0, processedPages: 0 },
    cancellingJobs: new Set(), jobStatusSnapshot: null, jobSearch: '', jobPage: 1,
    jobOutcomeFilter: 'all', toastTimer: null, toastFadeTimer: null,
  };
})(window);
