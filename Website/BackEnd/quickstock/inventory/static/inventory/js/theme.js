(function () {
    const OVERLAY_FLAG = "__qsErrorOverlayInitialized";
    const root = document.documentElement;
    const pref = root.dataset.theme || document.body?.dataset.theme || "system";

    const initErrorOverlay = () => {
        if (window[OVERLAY_FLAG]) return;
        window[OVERLAY_FLAG] = true;

        const ensureOverlay = () => {
            if (document.getElementById("qsErrorOverlay")) {
                return document.getElementById("qsErrorOverlay");
            }
            if (!document.body) return null;

            const overlay = document.createElement("div");
            overlay.id = "qsErrorOverlay";
            overlay.className = "qs-error-overlay";
            overlay.setAttribute("hidden", "hidden");
            overlay.innerHTML = `
                <div class="qs-error-overlay__backdrop" data-qs-error-close="true"></div>
                <div class="qs-error-overlay__dialog" role="alertdialog" aria-modal="true" aria-labelledby="qsErrorOverlayTitle">
                    <p class="qs-error-overlay__eyebrow">QuickStock detected a problem</p>
                    <h2 id="qsErrorOverlayTitle" class="qs-error-overlay__title">Something went wrong</h2>
                    <p class="qs-error-overlay__message" id="qsErrorOverlayMessage">An unexpected error interrupted this page.</p>
                    <div class="qs-error-overlay__actions">
                        <button type="button" class="qs-error-overlay__button qs-error-overlay__button--primary" data-qs-error-reload="true">Reload page</button>
                        <button type="button" class="qs-error-overlay__button" data-qs-error-close="true">Dismiss</button>
                    </div>
                </div>
            `;

            overlay.addEventListener("click", (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.matches("[data-qs-error-close='true']")) {
                    overlay.setAttribute("hidden", "hidden");
                }
                if (target.matches("[data-qs-error-reload='true']")) {
                    window.location.reload();
                }
            });

            document.body.appendChild(overlay);
            return overlay;
        };

        const showOverlay = ({ title, message } = {}) => {
            const overlay = ensureOverlay();
            if (!overlay) return;
            const titleNode = overlay.querySelector("#qsErrorOverlayTitle");
            const messageNode = overlay.querySelector("#qsErrorOverlayMessage");
            if (titleNode) titleNode.textContent = title || "Something went wrong";
            if (messageNode) {
                messageNode.textContent = message || "An unexpected error interrupted this page. You can reload now or dismiss this notice and continue carefully.";
            }
            overlay.removeAttribute("hidden");
        };

        window.addEventListener("quickstock:error", (event) => {
            showOverlay(event.detail || {});
        });

        window.addEventListener("error", (event) => {
            showOverlay({
                title: "Page error detected",
                message: event.message || "An unexpected script error interrupted this page.",
            });
        });

        window.addEventListener("unhandledrejection", (event) => {
            const reason = event.reason;
            const message = typeof reason === "string"
                ? reason
                : reason && typeof reason.message === "string"
                    ? reason.message
                    : "An unexpected background task failed.";
            showOverlay({
                title: "Background action failed",
                message,
            });
        });

        if (typeof window.fetch === "function") {
            const originalFetch = window.fetch.bind(window);
            window.fetch = async (...args) => {
                try {
                    const response = await originalFetch(...args);
                    if (response.status >= 500) {
                        showOverlay({
                            title: "Server error detected",
                            message: `The server responded with error ${response.status}. Reload the page and try again.`,
                        });
                    }
                    return response;
                } catch (error) {
                    showOverlay({
                        title: "Network connection problem",
                        message: error && error.message ? error.message : "QuickStock could not reach the server.",
                    });
                    throw error;
                }
            };
        }
    };

    const applyTheme = (mode) => {
        root.setAttribute("data-theme-applied", mode);
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initErrorOverlay, { once: true });
    } else {
        initErrorOverlay();
    }

    if (pref === "system") {
        const mq = window.matchMedia("(prefers-color-scheme: dark)");
        applyTheme(mq.matches ? "dark" : "light");
        const handler = (event) => applyTheme(event.matches ? "dark" : "light");
        if (typeof mq.addEventListener === "function") {
            mq.addEventListener("change", handler);
        } else if (typeof mq.addListener === "function") {
            mq.addListener(handler);
        }
        return;
    }

    applyTheme(pref || "light");
})();
