document.addEventListener("DOMContentLoaded", function () {
  const panel = document.getElementById("cashMovementPanel");
  const overlay = document.getElementById("cashMovementOverlay");
  const closeBtn = document.getElementById("cashMovementClose");
  const form = document.getElementById("cashMovementForm");
  const amount = document.getElementById("cashMovementAmount");
  const reason = document.getElementById("cashMovementReason");
  const reference = document.getElementById("cashMovementReference");
  const directionField = document.getElementById("cashMovementDirection");
  const typeSelect = document.getElementById("cashMovementType");
  const errorBox = document.getElementById("cashMovementError");
  const submitBtn = document.getElementById("cashMovementSubmit");
  const triggers = Array.from(
    document.querySelectorAll(".cash-movement-trigger"),
  );
  const tabs = Array.from(document.querySelectorAll(".cash-movement-tab"));

  if (!panel || !overlay || !form || !amount || !triggers.length) return;

  const endpoint = panel.dataset.endpoint;
  let lastFocused = null;

  const setDirection = (direction) => {
    directionField.value = direction;
    tabs.forEach((tab) => {
      const active = tab.dataset.direction === direction;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    // Keep the type select consistent with the chosen direction. A refund is
    // always money leaving the drawer, so the tabs drive the type, not the
    // other way round.
    if (typeSelect) {
      const refundOption = typeSelect.querySelector('option[value="refund"]');
      if (direction === "in" && refundOption) refundOption.hidden = true;
      if (direction === "out" && refundOption) refundOption.hidden = false;
      const current = typeSelect.value;
      if (direction === "in" && current !== "cash_in")
        typeSelect.value = "cash_in";
      if (direction === "out" && current === "cash_in")
        typeSelect.value = "cash_out";
    }
  };

  const showError = (msg) => {
    if (!errorBox) return;
    errorBox.textContent = msg || "";
    errorBox.hidden = !msg;
  };

  const openPanel = () => {
    if (panel.classList.contains("is-open")) return;
    lastFocused = document.activeElement;
    overlay.hidden = false;
    panel.classList.add("is-open");
    panel.setAttribute("aria-hidden", "false");
    document.body.classList.add("cash-movement-open");
    showError("");
    requestAnimationFrame(() => amount && amount.focus());
  };

  const closePanel = () => {
    panel.classList.remove("is-open");
    panel.setAttribute("aria-hidden", "true");
    overlay.hidden = true;
    document.body.classList.remove("cash-movement-open");
    showError("");
    if (lastFocused && typeof lastFocused.focus === "function") {
      lastFocused.focus();
      lastFocused = null;
    }
  };

  triggers.forEach((trigger) => trigger.addEventListener("click", openPanel));
  tabs.forEach((tab) =>
    tab.addEventListener("click", () => setDirection(tab.dataset.direction)),
  );

  if (typeSelect) {
    typeSelect.addEventListener("change", () => {
      // Choosing "refund" implies an outflow regardless of the active tab.
      if (typeSelect.value === "refund") setDirection("out");
    });
  }

  if (closeBtn) closeBtn.addEventListener("click", closePanel);
  overlay.addEventListener("click", closePanel);

  document.addEventListener("keydown", (e) => {
    if (!panel.classList.contains("is-open")) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closePanel();
    }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    showError("");

    const value = parseFloat(amount.value);
    if (!Number.isFinite(value) || value <= 0) {
      showError("Enter an amount greater than zero.");
      amount.focus();
      return;
    }
    if (!reason.value.trim()) {
      showError("A reason is required for every cash movement.");
      reason.focus();
      return;
    }

    const payload = new FormData();
    payload.append("direction", directionField.value);
    payload.append("movement_type", typeSelect ? typeSelect.value : "cash_out");
    payload.append("amount", amount.value);
    payload.append("reason", reason.value.trim());
    payload.append("reference", reference.value.trim());
    payload.append(
      "csrfmiddlewaretoken",
      form.querySelector("[name=csrfmiddlewaretoken]").value,
    );

    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = "Recording…";

    fetch(endpoint, {
      method: "POST",
      body: payload,
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    })
      .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || !data.ok) {
          showError((data && data.error) || "Could not record the movement.");
          submitBtn.disabled = false;
          submitBtn.textContent = originalText;
          return;
        }
        // The drawer balance changed, so a reload is the honest way to
        // show the new expected cash rather than guessing at it.
        window.location.reload();
      })
      .catch(() => {
        showError("Could not reach the server. Try again in a moment.");
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
      });
  });

  setDirection("out");
});
