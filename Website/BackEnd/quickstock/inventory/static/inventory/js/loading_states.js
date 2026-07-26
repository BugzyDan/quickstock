(function() {
    const root = document.documentElement;
    const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let progressTimer = null;
    let hideTimer = null;
    let failSafeTimer = null;

    function ensureProgressBar() {
        let bar = document.getElementById("quickstock-page-progress");
        if (bar) return bar;

        bar = document.createElement("div");
        bar.id = "quickstock-page-progress";
        bar.className = "qs-page-progress";
        bar.setAttribute("aria-hidden", "true");
        bar.innerHTML = '<span class="qs-page-progress-fill"></span>';
        document.body.appendChild(bar);
        return bar;
    }

    function startProgress() {
        if (!document.body) return;
        window.clearTimeout(hideTimer);
        window.clearTimeout(failSafeTimer);
        const bar = ensureProgressBar();
        const fill = bar.querySelector(".qs-page-progress-fill");
        if (!fill) return;

        bar.classList.add("is-visible");
        fill.style.width = reducedMotion ? "96%" : "36%";
        window.clearTimeout(progressTimer);
        if (!reducedMotion) {
            progressTimer = window.setTimeout(() => {
                fill.style.width = "82%";
            }, 120);
        }
        root.classList.add("qs-navigation-busy");
        failSafeTimer = window.setTimeout(finishProgress, 15000);
    }

    function finishProgress() {
        const bar = document.getElementById("quickstock-page-progress");
        if (!bar) return;

        const fill = bar.querySelector(".qs-page-progress-fill");
        window.clearTimeout(progressTimer);
        window.clearTimeout(failSafeTimer);
        if (fill) fill.style.width = "100%";
        hideTimer = window.setTimeout(() => {
            bar.classList.remove("is-visible");
            if (fill) fill.style.width = "0";
            root.classList.remove("qs-navigation-busy");
        }, reducedMotion ? 0 : 220);
    }

    function isModifiedClick(event) {
        return event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0;
    }

    function shouldIgnoreElement(el) {
        return Boolean(el && el.closest(
            "[data-loading-ignore], [data-no-loading], [data-loading-opt-out], .js-no-loading, " +
            ".nav-search-trigger, .nav-search-right, .nav-search-btn, .nav-search-link"
        ));
    }

    function isNavigatingLink(anchor) {
        if (!anchor || shouldIgnoreElement(anchor)) return false;
        if (anchor.target && anchor.target.toLowerCase() !== "_self") return false;
        if (anchor.hasAttribute("download")) return false;
        if (anchor.getAttribute("role") === "button") return false;

        const href = anchor.getAttribute("href") || "";
        if (!href || href.startsWith("#")) return false;
        if (/^(javascript:|mailto:|tel:)/i.test(href)) return false;

        const url = new URL(anchor.href, window.location.href);
        if (url.origin !== window.location.origin) return false;
        return url.pathname !== window.location.pathname || url.search !== window.location.search;
    }

    function getSubmitButton(form, event) {
        if (event && event.submitter && event.submitter.matches("button, input[type='submit'], input[type='image']")) {
            return event.submitter;
        }

        const active = document.activeElement;
        if (active && form.contains(active) && active.matches("button, input[type='submit'], input[type='image']")) {
            return active;
        }

        return form.querySelector("button[type='submit'], input[type='submit'], input[type='image'], button:not([type])");
    }

    function mirrorSubmitterValue(form, button) {
        if (!button || !button.name || button.dataset.qsSubmitterMirrored === "true") return;
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = button.name;
        hidden.value = button.value || "";
        hidden.dataset.qsSubmitterMirror = "true";
        form.appendChild(hidden);
        button.dataset.qsSubmitterMirrored = "true";
    }

    function setButtonLoading(button, loading, label) {
        if (!button) return;

        if (loading) {
            if (button.dataset.qsLoading === "true") return;

            button.dataset.qsLoading = "true";
            button.dataset.qsLoadingOriginalHtml = button.innerHTML;
            button.dataset.qsLoadingOriginalValue = button.value || "";
            button.dataset.qsLoadingOriginalDisabled = button.disabled ? "true" : "false";
            button.style.minWidth = `${Math.ceil(button.getBoundingClientRect().width)}px`;
            button.classList.add("qs-button-loading", "is-loading");
            button.setAttribute("aria-busy", "true");
            button.disabled = true;

            const loadingLabel = label || button.dataset.loadingLabel || button.dataset.loadingText || "Working...";
            if (button.matches("input")) {
                button.value = loadingLabel;
                return;
            }

            const spinner = document.createElement("span");
            spinner.className = "qs-button-spinner";
            spinner.setAttribute("aria-hidden", "true");

            const text = document.createElement("span");
            text.className = "qs-button-loading-label";
            text.textContent = loadingLabel;

            button.replaceChildren(spinner, text);
            return;
        }

        if (button.dataset.qsLoading !== "true") return;
        if (button.matches("input")) {
            button.value = button.dataset.qsLoadingOriginalValue || "";
        } else {
            button.innerHTML = button.dataset.qsLoadingOriginalHtml || button.textContent || "";
        }
        button.disabled = button.dataset.qsLoadingOriginalDisabled === "true";
        button.style.minWidth = "";
        button.classList.remove("qs-button-loading", "is-loading");
        button.removeAttribute("aria-busy");
        delete button.dataset.qsLoading;
        delete button.dataset.qsLoadingOriginalHtml;
        delete button.dataset.qsLoadingOriginalValue;
        delete button.dataset.qsLoadingOriginalDisabled;
        delete button.dataset.qsSubmitterMirrored;
    }

    function shouldHandleForm(form) {
        if (!form || shouldIgnoreElement(form)) return false;
        if (form.dataset.loadingBehavior === "manual") return false;
        if (form.method && form.method.toLowerCase() === "dialog") return false;
        if (form.target && form.target.toLowerCase() !== "_self") return false;
        if (form.id === "globalSearchPanelForm") return false;
        return true;
    }

    document.addEventListener("click", (event) => {
        if (isModifiedClick(event)) return;
        const anchor = event.target.closest && event.target.closest("a[href]");
        if (isNavigatingLink(anchor)) startProgress();
    }, true);

    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!shouldHandleForm(form)) return;
        if (event.defaultPrevented) return;

        const button = getSubmitButton(form, event);
        if (shouldIgnoreElement(button)) return;
        mirrorSubmitterValue(form, button);
        setButtonLoading(button, true, button ? button.dataset.loadingLabel : "");
        startProgress();
    });

    window.addEventListener("beforeunload", startProgress);
    window.addEventListener("pagehide", startProgress);
    window.addEventListener("pageshow", finishProgress);
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") finishProgress();
    });

    window.QuickStockLoading = {
        start: startProgress,
        finish: finishProgress,
        setButtonLoading,
        clearButtonLoading(button) {
            setButtonLoading(button, false);
        }
    };
})();
