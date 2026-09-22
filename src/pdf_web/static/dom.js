/* Small DOM and presentation utilities shared by UI modules. */
(function installPdfWebDom(global) {
  'use strict';
  function el(id) { return global.document.getElementById(id); }
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
    if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join('; ');
    return 'Request failed.';
  }
  global.PdfWebDom = Object.freeze({ el, formatBytes, describeError });
})(window);
