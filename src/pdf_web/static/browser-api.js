/* Shared browser API transport for the PDF web portal. */
(function installPdfWebApi(global) {
  'use strict';

  global.PdfWebApi = Object.freeze({
    request(path, options) {
      return global.fetch(path, options);
    },
  });
})(window);
