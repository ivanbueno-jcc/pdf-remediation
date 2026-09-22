/* Confirmation-dialog behavior, isolated from application actions. */
(function installPdfWebDialogs(global) {
  'use strict';
  function confirmAction(titleText, copyText, confirmText) {
    const byId = global.PdfWebDom.el;
    const dialog = byId('delete-dialog'), title = byId('delete-dialog-title');
    const copy = byId('delete-dialog-copy'), confirm = byId('delete-confirm');
    const cancel = byId('delete-cancel');
    title.textContent = titleText;
    copy.textContent = copyText;
    const oldLabel = confirm.textContent;
    confirm.textContent = confirmText;
    return new Promise((resolve) => {
      const focused = global.document.activeElement;
      dialog.addEventListener('close', () => {
        confirm.textContent = oldLabel;
        if (focused && focused.isConnected && focused.focus) focused.focus();
        resolve(dialog.returnValue === 'confirm');
      }, { once: true });
      confirm.onclick = () => { dialog.returnValue = 'confirm'; dialog.close(); };
      cancel.onclick = () => { dialog.returnValue = 'cancel'; dialog.close(); };
      dialog.returnValue = '';
      dialog.showModal();
      cancel.focus();
    });
  }
  global.PdfWebDialogs = Object.freeze({ confirmAction });
})(window);
