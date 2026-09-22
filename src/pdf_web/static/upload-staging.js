/* Pure upload validation so batch rules can be tested independently. */
(function installPdfWebUploadStaging(global) {
  'use strict';
  function validate(file, accepted, limits, totalBytes, formatBytes) {
    if (!file.name.toLowerCase().endsWith('.pdf')) return { ignored: true };
    if (accepted.some((item) => item.file.name === file.name && item.file.size === file.size)) {
      return { reason: 'This file is already in the batch.' };
    }
    if (file.size > limits.max_file_bytes) {
      return { reason: 'File exceeds the ' + formatBytes(limits.max_file_bytes) + ' per-file limit.' };
    }
    if (accepted.length >= limits.max_files) {
      return { reason: 'The batch already contains the maximum of ' + limits.max_files + ' files.' };
    }
    if (totalBytes + file.size > limits.max_submission_bytes) {
      return { reason: 'Adding it would exceed the ' + formatBytes(limits.max_submission_bytes) + ' batch limit.' };
    }
    return { accepted: true };
  }
  global.PdfWebUploadStaging = Object.freeze({ validate });
})(window);
