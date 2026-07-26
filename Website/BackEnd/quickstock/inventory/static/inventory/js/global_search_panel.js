document.addEventListener("DOMContentLoaded", function () {
    const panel = document.getElementById("globalSearchPanel");
    const overlay = document.getElementById("globalSearchOverlay");
    const closeBtn = document.getElementById("globalSearchClose");
    const form = document.getElementById("globalSearchPanelForm");
    const input = document.getElementById("globalSearchPanelInput");
    const scopeSelect = document.getElementById("globalSearchPanelScope");
    const meta = document.getElementById("globalSearchPanelMeta");
    const results = document.getElementById("globalSearchPanelResults");
    const fullLink = document.getElementById("globalSearchOpenFull");

    if (!panel || !overlay || !form || !input || !scopeSelect || !meta || !results || !fullLink) {
        return;
    }

    const apiUrl = panel.dataset.apiUrl || "/search/panel/";
    const fallbackSearchUrl = panel.dataset.fullSearchUrl || "/search/";
    const triggerSelector = "a.nav-search-trigger, a.nav-search-right, a.nav-search-btn, a.nav-search-link";
    const triggers = Array.from(document.querySelectorAll(triggerSelector));

    let debounceTimer = null;

    const setFullLink = (q, scope) => {
        const params = new URLSearchParams();
        if (q) params.set("q", q);
        if (scope && scope !== "all") params.set("scope", scope);
        const suffix = params.toString() ? `?${params.toString()}` : "";
        fullLink.href = `${fallbackSearchUrl}${suffix}`;
    };

    const openPanel = () => {
        const nav = document.getElementById("main-nav");
        const navToggle = document.getElementById("navToggle");
        if (nav) {
            nav.classList.remove("mobile-active");
            nav.setAttribute("aria-hidden", "true");
        }
        if (navToggle) {
            navToggle.classList.remove("is-open");
            navToggle.setAttribute("aria-expanded", "false");
        }
        document.body.classList.remove("qs-mobile-nav-open");
        overlay.hidden = false;
        panel.classList.add("is-open");
        panel.setAttribute("aria-hidden", "false");
        document.body.classList.add("global-search-open");
        requestAnimationFrame(() => {
            if (input) input.focus();
        });
    };

    const closePanel = () => {
        panel.classList.remove("is-open");
        panel.setAttribute("aria-hidden", "true");
        overlay.hidden = true;
        document.body.classList.remove("global-search-open");
    };

    const emptyNode = (txt) => {
        const p = document.createElement("p");
        p.className = "global-search-placeholder";
        p.textContent = txt;
        return p;
    };

    const renderSection = (sectionData) => {
        if (!sectionData || !sectionData.items || sectionData.items.length === 0) return null;
        const section = document.createElement("section");
        section.className = "global-search-result-section";

        const head = document.createElement("div");
        head.className = "global-search-result-head";
        const heading = document.createElement("h4");
        heading.textContent = sectionData.title;
        head.appendChild(heading);

        const count = document.createElement("span");
        count.className = "global-search-chip";
        count.textContent = String(sectionData.count || sectionData.items.length || 0);
        head.appendChild(count);
        section.appendChild(head);

        if (sectionData.description) {
            const desc = document.createElement("p");
            desc.className = "global-search-section-copy";
            desc.textContent = sectionData.description;
            section.appendChild(desc);
        }

        const wrap = document.createElement("div");
        wrap.className = "global-search-result-wrap";
        sectionData.items.forEach((item) => {
            const card = document.createElement(item.url ? "a" : "div");
            card.className = "global-search-record";
            if (item.url) card.href = item.url;

            const cardHead = document.createElement("div");
            cardHead.className = "global-search-record-head";

            const title = document.createElement("strong");
            title.textContent = item.title || "-";
            cardHead.appendChild(title);

            if (item.status) {
                const status = document.createElement("span");
                status.textContent = item.status;
                cardHead.appendChild(status);
            }
            card.appendChild(cardHead);

            if (item.subtitle) {
                const subtitle = document.createElement("p");
                subtitle.className = "global-search-record-subtitle";
                subtitle.textContent = item.subtitle;
                card.appendChild(subtitle);
            }

            if (item.meta) {
                const metaLine = document.createElement("p");
                metaLine.className = "global-search-record-meta";
                metaLine.textContent = item.meta;
                card.appendChild(metaLine);
            }

            if (item.detail) {
                const detail = document.createElement("p");
                detail.className = "global-search-record-detail";
                detail.textContent = item.detail;
                card.appendChild(detail);
            }

            if (item.action_label) {
                const action = document.createElement("div");
                action.className = "global-search-record-action";
                action.textContent = item.action_label;
                card.appendChild(action);
            }

            wrap.appendChild(card);
        });
        section.appendChild(wrap);

        return section;
    };

    const renderMeta = (data) => {
        meta.innerHTML = "";
        const sectionCounts = Array.isArray(data.section_counts) ? data.section_counts.filter((entry) => entry.count) : [];
        const chips = [`Total: ${data.total_count || 0}`].concat(
            sectionCounts.slice(0, 6).map((entry) => `${entry.title}: ${entry.count}`)
        );
        chips.forEach((txt) => {
            const span = document.createElement("span");
            span.className = "global-search-chip";
            span.textContent = txt;
            meta.appendChild(span);
        });
    };

    const renderResults = (data, q, scope) => {
        results.innerHTML = "";

        if (!q) {
            results.appendChild(emptyNode("Start typing to search products, customers, sales, movements, suppliers, and payments."));
            return;
        }

        const hasAny = (data.total_count || 0) > 0;

        if (!data.can_view_finance && !["customers", "products", "locations"].includes(scope)) {
            const note = document.createElement("div");
            note.className = "global-search-role-note";
            note.textContent = "Finance-oriented objects are visible to Admin and Manager roles only.";
            results.appendChild(note);
        }

        if (!hasAny) {
            results.appendChild(emptyNode(`No business objects found for "${q}".`));
            return;
        }

        (data.sections || []).forEach((sectionData) => {
            const section = renderSection(sectionData);
            if (section) results.appendChild(section);
        });
    };

    const runSearch = () => {
        const q = (input.value || "").trim();
        const scope = (scopeSelect.value || "all").trim().toLowerCase();
        setFullLink(q, scope);

        if (!q) {
            meta.innerHTML = "";
            renderResults({}, "", scope);
            return;
        }

        fetch(`${apiUrl}?q=${encodeURIComponent(q)}&scope=${encodeURIComponent(scope)}`, {
            headers: { Accept: "application/json" },
        })
            .then((res) => {
                if (!res.ok) throw new Error("search_failed");
                return res.json();
            })
            .then((data) => {
                renderMeta(data);
                renderResults(data, q, scope);
            })
            .catch(() => {
                meta.innerHTML = "";
                results.innerHTML = "";
                results.appendChild(emptyNode("Search is unavailable right now. Try again in a moment."));
            });
    };

    const runSearchDebounced = () => {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(runSearch, 250);
    };

    triggers.forEach((trigger) => {
        trigger.addEventListener("click", (e) => {
            e.preventDefault();
            openPanel();
            runSearchDebounced();
        });
    });

    form.addEventListener("submit", (e) => {
        e.preventDefault();
        runSearch();
    });
    input.addEventListener("input", runSearchDebounced);
    scopeSelect.addEventListener("change", runSearchDebounced);

    closeBtn.addEventListener("click", closePanel);
    overlay.addEventListener("click", closePanel);
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && panel.classList.contains("is-open")) {
            closePanel();
        }
    });
});
