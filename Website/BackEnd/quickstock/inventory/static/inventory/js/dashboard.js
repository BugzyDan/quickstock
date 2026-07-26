(function () {
    const onMediaQueryChange = (mq, handler) => {
        if (mq && typeof mq.addEventListener === 'function') mq.addEventListener('change', handler);
        else if (mq && typeof mq.addListener === 'function') mq.addListener(handler);
    };

    // --- 1. CONFIG & THEME ---
    const config = window.DashboardConfig || { themePref: "system" };
    const root = document.documentElement;
    const setTheme = (mode) => { root.dataset.themeApplied = mode; };

    const initTheme = () => {
        const saved = config.themePref;
        if (saved === "system") {
            const mq = window.matchMedia("(prefers-color-scheme: dark)");
            setTheme(mq.matches ? "dark" : "light");
            onMediaQueryChange(mq, (e) => setTheme(e.matches ? "dark" : "light"));
        } else {
            setTheme(saved);
        }
    };

    // --- 2. CORE DASHBOARD LOGIC ---
    const initDashboard = () => {
            // A. DROPDOWN HANDLER
        document.addEventListener('click', (e) => {
            const userBtn = e.target.closest('#userMenuBtn');
            const userMenu = document.getElementById('userDropdown');

            if (userBtn && userMenu) {
                e.stopPropagation();
                // Toggle inline display
                if (userMenu.style.display === 'none' || userMenu.style.display === '') {
                    userMenu.style.display = 'block';
                } else {
                    userMenu.style.display = 'none';
                }
            } else if (userMenu && !userMenu.contains(e.target)) {
                // Hide if clicking anywhere else on the document
                userMenu.style.display = 'none';
            }
        });
        // A2. DASHBOARD LOCATION FILTER
        const locationSelect = document.getElementById('dashboardLocationSelect');
        const locationForm = document.getElementById('dashboardLocationForm');
        if (locationSelect && locationForm) {
            locationSelect.addEventListener('change', () => {
                if (locationSelect.dataset.autosubmit === 'true') {
                    locationForm.submit();
                }
            });
        }

        // B. PROGRESS BAR
        const bar = document.querySelector('.progress-fill');
        if (bar) {
            const targetWidth = bar.dataset.targetWidth || bar.style.width || '0%';
            bar.style.width = '0%';
            requestAnimationFrame(() => {
                setTimeout(() => {
                    bar.style.transition = 'width 1.2s cubic-bezier(0.33, 1, 0.68, 1)';
                    bar.style.width = targetWidth;
                }, 100);
            });
        }

        // C. KPI CARDS
        document.querySelectorAll('.kpi-card').forEach(card => {
            card.style.transition = 'transform 0.2s cubic-bezier(0.4, 0, 0.2, 1)';
            card.addEventListener('mouseenter', () => card.style.transform = 'translateY(-5px)');
            card.addEventListener('mouseleave', () => card.style.transform = 'translateY(0)');
        });

        // D. NAVIGATION HANDLER (Mobile Toggle)
        const navToggle = document.getElementById('navToggle');
        const nav = document.getElementById('main-nav');
        if (navToggle && nav && document.documentElement.dataset.qsShellBound !== 'true') {
            navToggle.addEventListener('click', (e) => {
                e.stopPropagation();
                nav.classList.toggle('mobile-active');
                navToggle.classList.toggle('is-open');
            });

            document.addEventListener('click', (e) => {
                // If user clicked inside any sales dropdown item, do not close the mobile nav.
                if (e.target && e.target.closest && e.target.closest('[data-sales-nav-item]')) {
                    return;
                }
                if (nav.classList.contains('mobile-active') && !nav.contains(e.target) && !navToggle.contains(e.target)) {
                    nav.classList.remove('mobile-active');
                    navToggle.classList.remove('is-open');
                }
            });
        }

        // E. STICKY ALIGNMENT (The Gap Killer)
        const syncNavPosition = () => {
            const header = document.querySelector('header');
            const mainNav = document.getElementById('main-nav');
            if (header && mainNav) {
                // Use offsetHeight for a more stable integer measurement
                const headerHeight = header.offsetHeight;
                document.documentElement.style.setProperty('--qs-header-height', `${headerHeight}px`);
                mainNav.style.top = `${headerHeight}px`;
            }
        };

        // 1. Run immediately
        syncNavPosition();

        // 2. Listen for window changes
        window.addEventListener('resize', syncNavPosition);

        // 3. Listen for image/resource loads (Crucial for logos)
        window.addEventListener('load', syncNavPosition);

        // 4. Observer: if header content changes dynamically, keep nav flush
        const headerEl = document.querySelector('header');
        if (headerEl) {
            const observer = new MutationObserver(syncNavPosition);
            observer.observe(headerEl, { attributes: true, childList: true, subtree: true });
        }
    };

    // Execution
    initTheme();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboard);
    } else {
        initDashboard();
    }
})();
