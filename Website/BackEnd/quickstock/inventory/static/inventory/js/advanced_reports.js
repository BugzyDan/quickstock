/**
 * QuickStock Advanced Analytics Terminal - v1.2 (2026)
 * Fixes: Dynamic scaling colors for Midnight Theme & JMD Logic
 */
(function() {
    const config = window.AnalyticsConfig || {};
    const onMediaQueryChange = (mq, handler) => {
        if (mq && typeof mq.addEventListener === 'function') mq.addEventListener('change', handler);
        else if (mq && typeof mq.addListener === 'function') mq.addListener(handler);
    };
    const findNavToggle = () => (
        document.getElementById('navToggle') ||
        document.querySelector('.mobile-nav-toggle') ||
        document.querySelector('.nav-toggle')
    );

    // --- 1. THEME ENGINE & DYNAMIC CHART COLORS ---
    const getThemeColors = () => {
        const isDark = document.documentElement.classList.contains('force-dark') || 
                       document.documentElement.dataset.themeApplied === 'dark';
        return {
            text: isDark ? '#94a3b8' : '#64748b',
            grid: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
            accent: '#3b82f6'
        };
    };

    const applyTheme = (mode) => {
        document.documentElement.dataset.themeApplied = mode;
        // Optional: Trigger chart update if theme changes live
    };
    
    if (config.themePref === "system") {
        const mq = window.matchMedia("(prefers-color-scheme: dark)");
        applyTheme(mq.matches ? "dark" : "light");
        onMediaQueryChange(mq, e => applyTheme(e.matches ? "dark" : "light"));
    } else {
        applyTheme(config.themePref);
    }

    // Standardized JMD Formatter
    const currencyFormatter = new Intl.NumberFormat('en-JM', {
        style: 'currency',
        currency: 'JMD',
        maximumFractionDigits: 0
    });

    const initAdvancedReports = function() {
        const colors = getThemeColors();

        // --- 2. UI COMPONENTS (Dropdown) ---
        const menuBtn = document.getElementById('userMenuBtn');
        const dropdown = document.getElementById('userDropdown');
        
        if (menuBtn && dropdown) {
            menuBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block';
            });
            document.addEventListener('click', () => dropdown.style.display = 'none');
        }

        // --- 2B. APP SHELL (Sticky Nav + Mobile Toggle) ---
        const syncStickyOffsets = () => {
            const header = document.querySelector('header');
            const nav = document.getElementById('main-nav');
            if (header && nav) {
                const headerHeight = header.offsetHeight;
                document.documentElement.style.setProperty('--qs-header-height', `${headerHeight}px`);
                nav.style.top = `${headerHeight}px`;
            }
        };

        syncStickyOffsets();
        window.addEventListener('resize', syncStickyOffsets);
        window.addEventListener('load', syncStickyOffsets);
        const headerEl = document.querySelector('header');
        if (headerEl) {
            const observer = new MutationObserver(syncStickyOffsets);
            observer.observe(headerEl, { attributes: true, childList: true, subtree: true });
        }

        const navToggle = findNavToggle();
        const nav = document.getElementById('main-nav');
        if (navToggle && nav && document.documentElement.dataset.qsShellBound !== 'true') {
            navToggle.setAttribute('aria-expanded', nav.classList.contains('mobile-active') ? 'true' : 'false');
            navToggle.addEventListener('click', (e) => {
                e.stopPropagation();
                nav.classList.toggle('mobile-active');
                navToggle.classList.toggle('is-open');
                navToggle.setAttribute('aria-expanded', nav.classList.contains('mobile-active') ? 'true' : 'false');
            });

            document.addEventListener('click', (e) => {
                if (nav.classList.contains('mobile-active') && !nav.contains(e.target) && !navToggle.contains(e.target)) {
                    nav.classList.remove('mobile-active');
                    navToggle.classList.remove('is-open');
                    navToggle.setAttribute('aria-expanded', 'false');
                }
            });

            window.addEventListener('resize', () => {
                if (window.innerWidth > 900 && nav.classList.contains('mobile-active')) {
                    nav.classList.remove('mobile-active');
                    navToggle.classList.remove('is-open');
                    navToggle.setAttribute('aria-expanded', 'false');
                }
            });
        }

        // Global Chart Defaults - Synced with Theme
        Chart.defaults.font.family = "'Inter', sans-serif";
        Chart.defaults.color = colors.text;
        Chart.defaults.borderColor = colors.grid;

        // --- 3. REVENUE VELOCITY ---
        const revCtx = document.getElementById('revenueChart');
        if (revCtx && Object.keys(config.revenueTrends || {}).length > 0) {
            new Chart(revCtx, {
                type: 'line',
                data: {
                    labels: Object.keys(config.revenueTrends),
                    datasets: [{
                        label: 'Gross Revenue',
                        data: Object.values(config.revenueTrends),
                        borderColor: colors.accent,
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 3,
                        pointRadius: 0,
                        pointHoverRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { 
                            grid: { color: colors.grid },
                            ticks: { callback: (val) => '$' + (val >= 1000 ? (val / 1000) + 'k' : val) } 
                        },
                        x: { grid: { display: false } }
                    }
                }
            });
        }

        // --- 4. PAYABLES PULSE (Supplier Ledger) ---
        const payCtx = document.getElementById('payablesPulseChart');
        if (payCtx && config.payablesPulse && config.payablesPulse.labels && config.payablesPulse.labels.length > 0) {
            new Chart(payCtx, {
                data: {
                    labels: config.payablesPulse.labels,
                    datasets: [
                        { 
                            label: 'Balance', 
                            type: 'line', 
                            data: config.payablesPulse.balance, 
                            borderColor: colors.text, 
                            borderWidth: 2,
                            borderDash: [5, 5],
                            fill: false
                        },
                        { 
                            label: 'Debt', 
                            type: 'bar', 
                            data: config.payablesPulse.debit, 
                            backgroundColor: '#ef4444aa', // Transparent Red
                            borderRadius: 4
                        },
                        { 
                            label: 'Paid', 
                            type: 'bar', 
                            data: config.payablesPulse.credit, 
                            backgroundColor: '#10b981aa', // Transparent Green
                            borderRadius: 4
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { beginAtZero: true, grid: { color: colors.grid } },
                        x: { grid: { display: false } }
                    }
                }
            });
        }

        // --- 5. CASHIER PERFORMANCE ---
        const cashCtx = document.getElementById('cashierChart');
        if (cashCtx && config.cashierData && config.cashierData.values && config.cashierData.values.length > 0) {
            new Chart(cashCtx, {
                type: 'doughnut',
                data: {
                    labels: config.cashierData.labels,
                    datasets: [{
                        data: config.cashierData.values,
                        backgroundColor: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'],
                        borderWidth: 0,
                        hoverOffset: 15
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '75%',
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 15 } }
                    }
                }
            });
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAdvancedReports);
    } else {
        initAdvancedReports();
    }
})();
