/**
 * QUICKSTOCK JA 2026 - INVENTORY LOGIC
 * Includes: Theme Engine, Sticky Alignment, & UI Toggles
 */
(function () {
    const findNavToggle = () => (
        document.getElementById('navToggle') ||
        document.querySelector('.mobile-nav-toggle') ||
        document.querySelector('.nav-toggle')
    );
    const onMediaQueryChange = (mq, handler) => {
        if (mq && typeof mq.addEventListener === 'function') mq.addEventListener('change', handler);
        else if (mq && typeof mq.addListener === 'function') mq.addListener(handler);
    };
    const resolveThemePref = () => (
        document.documentElement.getAttribute('data-theme') ||
        document.documentElement.dataset.theme ||
        document.body?.getAttribute('data-theme') ||
        document.body?.dataset.theme ||
        'system'
    );

    // --- 1. THEME ENGINE ---
    const root = document.documentElement;
    const applyTheme = (mode) => root.setAttribute("data-theme-applied", mode);

    const initTheme = () => {
        const themePref = resolveThemePref();
        if (themePref === "system") {
            const mq = window.matchMedia("(prefers-color-scheme: dark)");
            applyTheme(mq.matches ? "dark" : "light");
            onMediaQueryChange(mq, (e) => applyTheme(e.matches ? "dark" : "light"));
        } else {
            applyTheme(themePref);
        }
    };

    // --- 2. UI INITIALIZATION ---
    const initInventoryUI = () => {
        
        // A. STICKY ALIGNMENT (The Gap Killer)
        const syncNav = () => {
            const header = document.querySelector('header');
            const nav = document.getElementById('main-nav');
            if (header && nav) {
                const headerHeight = header.offsetHeight;
                document.documentElement.style.setProperty('--qs-header-height', `${headerHeight}px`);
                nav.style.top = `${headerHeight}px`;
            }
        };

        syncNav();
        window.addEventListener('resize', syncNav);
        window.addEventListener('load', syncNav);
        const headerEl = document.querySelector('header');
        if (headerEl) {
            const observer = new MutationObserver(syncNav);
            observer.observe(headerEl, { attributes: true, childList: true, subtree: true });
        }

        // B. DROPDOWN HANDLER (Operator Menu)
        document.addEventListener('click', (e) => {
            const menuBtn = document.getElementById('userMenuBtn');
            const dropdown = document.getElementById('userDropdown');
            
            if (menuBtn && menuBtn.contains(e.target)) {
                e.stopPropagation();
                const isShowing = dropdown.style.display === 'block';
                dropdown.style.display = isShowing ? 'none' : 'block';
            } else if (dropdown && !dropdown.contains(e.target)) {
                dropdown.style.display = 'none';
            }
        });

        // C. BREAKDOWN TOGGLES (Table Rows)
        document.querySelectorAll(".breakdown-toggle").forEach((btn) => {
            btn.addEventListener("click", () => {
                const targetId = btn.getAttribute("data-target");
                const panel = document.getElementById(targetId);
                if (!panel) return;

                const isHidden = panel.style.display === "none" || panel.style.display === "";
                panel.style.display = isHidden ? "block" : "none";
                btn.textContent = isHidden ? "Hide" : "Breakdown";
                btn.classList.toggle('active', isHidden);
                btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
            });
        });

        // D. GLOBAL CONTROLS (Expand/Collapse All)
        const expandBtn = document.getElementById("expand-all");
        const collapseBtn = document.getElementById("collapse-all");

        if (expandBtn) {
            expandBtn.addEventListener("click", () => {
                document.querySelectorAll(".stock-breakdown").forEach(p => p.style.display = "block");
                document.querySelectorAll(".breakdown-toggle").forEach((b) => {
                    b.textContent = "Hide";
                    b.classList.add('active');
                    b.setAttribute('aria-expanded', 'true');
                });
            });
        }

        if (collapseBtn) {
            collapseBtn.addEventListener("click", () => {
                document.querySelectorAll(".stock-breakdown").forEach(p => p.style.display = "none");
                document.querySelectorAll(".breakdown-toggle").forEach((b) => {
                    b.textContent = "Breakdown";
                    b.classList.remove('active');
                    b.setAttribute('aria-expanded', 'false');
                });
            });
        }

        // E. PAGE METRICS (Visible result set)
        const quantityCells = Array.from(document.querySelectorAll('.inventory-qty-cell'));
        const lowStockCount = quantityCells.filter((cell) => {
            const value = Number(cell.dataset.quantity || 0);
            return value > 0 && value <= 10;
        }).length;
        const depletedCount = quantityCells.filter((cell) => Number(cell.dataset.quantity || 0) === 0).length;
        const lowStockEl = document.getElementById('inventory-low-stock-count');
        const depletedEl = document.getElementById('inventory-depleted-count');
        if (lowStockEl) lowStockEl.textContent = String(lowStockCount);
        if (depletedEl) depletedEl.textContent = String(depletedCount);

        // F. MOBILE NAV TOGGLE
        const navToggle = findNavToggle();
        const nav = document.getElementById('main-nav');
        if (navToggle && nav && document.documentElement.dataset.qsShellBound !== 'true') {
            navToggle.addEventListener('click', (e) => {
                e.stopPropagation();
                nav.classList.toggle('mobile-active');
                navToggle.classList.toggle('is-open');
            });

            document.addEventListener('click', (e) => {
                if (nav.classList.contains('mobile-active') && !nav.contains(e.target) && !navToggle.contains(e.target)) {
                    nav.classList.remove('mobile-active');
                    navToggle.classList.remove('is-open');
                }
            });
        }
    };

    // --- 3. EXECUTION ---
    initTheme();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initInventoryUI);
    } else {
        initInventoryUI();
    }
})();
