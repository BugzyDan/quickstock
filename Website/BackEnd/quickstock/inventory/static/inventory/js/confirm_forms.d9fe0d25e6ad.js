/**
 * Robust confirmation gate for destructive forms.
 *
 * Replaces fragile inline `onsubmit="return confirm(...)"` handlers, which
 * silently block submission whenever the browser suppresses the native dialog
 * (e.g. "Prevent this page from creating additional dialogs" is active, or the
 * page is embedded in a context where window.confirm is unavailable). When that
 * happens the form never submits and the button appears to "do nothing".
 *
 * Usage:
 *   <form method="post" data-confirm="Remove this supplier?">
 *
 * Behaviour: on submit, the first attempt is always cancelled and an in-page
 * dialog is shown. Confirming re-submits the form, bypassing this gate.
 */
(function () {
  "use strict";

  function buildDialog(message, onConfirm) {
    var overlay = document.createElement("div");
    overlay.className = "qs-confirm-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");

    var box = document.createElement("div");
    box.className = "qs-confirm-box";

    var text = document.createElement("p");
    text.className = "qs-confirm-text";
    text.textContent = message || "Are you sure you want to continue?";

    var actions = document.createElement("div");
    actions.className = "qs-confirm-actions";

    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "qs-confirm-btn qs-confirm-cancel";
    cancel.textContent = "Cancel";

    var confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "qs-confirm-btn qs-confirm-accept";
    confirm.textContent = "Delete";

    function close() {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
    }
    function onKey(event) {
      if (event.key === "Escape") close();
    }

    cancel.addEventListener("click", close);
    confirm.addEventListener("click", function () {
      close();
      onConfirm();
    });
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    document.addEventListener("keydown", onKey);

    actions.appendChild(cancel);
    actions.appendChild(confirm);
    box.appendChild(text);
    box.appendChild(actions);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    confirm.focus();
  }

  function attach(form) {
    if (form.dataset.qsConfirmBound === "true") return;
    form.dataset.qsConfirmBound = "true";

    form.addEventListener("submit", function (event) {
      if (form.dataset.qsConfirmApproved === "true") {
        delete form.dataset.qsConfirmApproved;
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();

      var message =
        form.dataset.confirm || "Are you sure you want to continue?";
      buildDialog(message, function () {
        form.dataset.qsConfirmApproved = "true";
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.submit();
        }
      });
    });
  }

  function init() {
    var forms = document.querySelectorAll("form[data-confirm]");
    Array.prototype.forEach.call(forms, attach);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
