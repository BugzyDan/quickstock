(function () {
    const configNode = document.getElementById("quickstockSessionSecurityConfig");
    if (!configNode) return;

    const timeoutSeconds = Number(configNode.dataset.idleTimeoutSeconds || 0);
    const heartbeatSeconds = Number(configNode.dataset.heartbeatSeconds || 0);
    const heartbeatUrl = configNode.dataset.heartbeatUrl || "";
    const expireUrl = configNode.dataset.expireUrl || "";
    const loginUrl = configNode.dataset.loginUrl || "/login/";
    const userId = configNode.dataset.userId || "anonymous";
    const syncKey = `quickstock:last-activity:${userId}`;

    if (!timeoutSeconds || !heartbeatSeconds || !heartbeatUrl || !expireUrl) return;

    const timeoutMs = timeoutSeconds * 1000;
    const heartbeatMs = heartbeatSeconds * 1000;
    const activityThrottleMs = 15000;

    let lastActivityAt = Date.now();
    let lastHeartbeatAt = 0;
    let lastBroadcastAt = 0;
    let logoutStarted = false;

    const getCookie = (name) => {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(";").shift();
        return "";
    };

    const getCsrfToken = () => configNode.dataset.csrfToken || getCookie("csrftoken");

    const syncActivity = (timestamp) => {
        if (!Number.isFinite(timestamp)) return;
        lastActivityAt = Math.max(lastActivityAt, timestamp);
    };

    const broadcastActivity = (timestamp) => {
        try {
            window.localStorage.setItem(syncKey, String(timestamp));
        } catch (error) {
            // Ignore storage failures in hardened browsers/private mode.
        }
    };

    const markActivity = () => {
        const now = Date.now();
        syncActivity(now);
        if (now - lastBroadcastAt < activityThrottleMs) return;
        lastBroadcastAt = now;
        broadcastActivity(now);
    };

    const redirectToLogin = () => {
        window.location.href = loginUrl;
    };

    const expireSession = async (reason) => {
        if (logoutStarted) return;
        logoutStarted = true;

        try {
            await fetch(expireUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-CSRFToken": getCsrfToken(),
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
                keepalive: true,
                body: new URLSearchParams({ reason }),
            });
        } catch (error) {
            // Redirect regardless of network outcome.
        }

        redirectToLogin();
    };

    const sendHeartbeat = async () => {
        if (logoutStarted || document.visibilityState === "hidden") return;
        if (!navigator.onLine) return;

        const now = Date.now();
        if (now - lastHeartbeatAt < heartbeatMs) return;
        lastHeartbeatAt = now;

        try {
            const response = await fetch(heartbeatUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-CSRFToken": getCsrfToken(),
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                },
                credentials: "same-origin",
                keepalive: true,
                body: new URLSearchParams({ heartbeat: "1" }),
            });

            if (response.status === 401) {
                let payload = null;
                try {
                    payload = await response.json();
                } catch (error) {
                    payload = null;
                }
                if (payload && payload.redirect_url) {
                    window.location.href = payload.redirect_url;
                    return;
                }
                redirectToLogin();
            }
        } catch (error) {
            // Keep waiting for the next loop; temporary failures should not boot the user.
        }
    };

    const checkLoop = () => {
        const now = Date.now();
        if (now - lastActivityAt >= timeoutMs) {
            expireSession("idle_timeout");
            return;
        }
        if (now - lastActivityAt < heartbeatMs) {
            sendHeartbeat();
        }
    };

    let initialStoredActivity = Number.NaN;
    try {
        initialStoredActivity = Number(window.localStorage.getItem(syncKey));
    } catch (error) {
        initialStoredActivity = Number.NaN;
    }
    if (Number.isFinite(initialStoredActivity) && initialStoredActivity > 0) {
        syncActivity(initialStoredActivity);
    } else {
        broadcastActivity(lastActivityAt);
    }

    ["mousemove", "mousedown", "keydown", "touchstart", "scroll"].forEach((eventName) => {
        window.addEventListener(eventName, markActivity, { passive: true });
    });

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") markActivity();
    });

    window.addEventListener("storage", (event) => {
        if (event.key === syncKey) {
            syncActivity(Number(event.newValue));
        }
    });

    markActivity();
    window.setInterval(checkLoop, 30000);
})();
