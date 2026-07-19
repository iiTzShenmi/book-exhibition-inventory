(() => {
  "use strict";

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-submit-form]").forEach((form) => {
      form.addEventListener("submit", () => {
        if (!form.checkValidity()) return;

        const submit = form.querySelector('button[type="submit"]');
        if (!submit || submit.disabled) return;

        submit.disabled = true;
        submit.textContent = submit.dataset.pendingLabel || "處理中...";
        form.setAttribute("aria-busy", "true");
      });
    });
  });
})();
