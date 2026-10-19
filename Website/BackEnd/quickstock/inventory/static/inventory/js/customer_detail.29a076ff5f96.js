document.addEventListener('DOMContentLoaded', function () {
    const isCustomerDetailPage = document.body.classList.contains('page-customer-detail');

    if (isCustomerDetailPage) {
        const recordsPanel = document.querySelector('[data-customer-records-panel]');
        const recordsLayout = recordsPanel ? recordsPanel.querySelector('[data-records-layout]') : null;
        const recordButtons = recordsPanel ? recordsPanel.querySelectorAll('[data-records-view]') : [];
        const recordSections = recordsPanel ? recordsPanel.querySelectorAll('[data-record-section]') : [];

        const setRecordsView = function (view) {
            const nextView = view || 'both';

            if (recordsLayout) {
                recordsLayout.dataset.recordsLayout = nextView;
            }

            recordButtons.forEach(function (button) {
                const isActive = button.dataset.recordsView === nextView;

                button.classList.toggle('is-active', isActive);
                button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });

            recordSections.forEach(function (section) {
                const shouldHide = nextView !== 'both' && section.dataset.recordSection !== nextView;

                section.toggleAttribute('hidden', shouldHide);
            });
        };

        recordButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                setRecordsView(button.dataset.recordsView);
            });
        });

        if (recordButtons.length && recordSections.length) {
            setRecordsView('both');
        }

        document.querySelectorAll('[data-document-url]').forEach(function (row) {
            row.addEventListener('click', function (event) {
                const interactiveTarget = event.target.closest('a, button, input, select, textarea, summary, details');

                if (interactiveTarget) {
                    return;
                }

                const documentUrl = row.dataset.documentUrl;

                if (documentUrl) {
                    window.location.href = documentUrl;
                }
            });
        });
    }

    // User Dropdown Toggle
    const menuBtn = document.getElementById('userMenuBtn');
    const dropdown = document.getElementById('userDropdown');

    if (menuBtn && dropdown) {
        menuBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            dropdown.style.display =
                dropdown.style.display === 'block' ? 'none' : 'block';
        });

        window.addEventListener('click', function (e) {
            if (
                !menuBtn.contains(e.target) &&
                !dropdown.contains(e.target)
            ) {
                dropdown.style.display = 'none';
            }
        });
    }

    // Mobile Navigation Toggle
    const navToggle = document.getElementById('navToggle');
    const nav = document.getElementById('main-nav');

    if (navToggle && nav && document.documentElement.dataset.qsShellBound !== 'true') {
        navToggle.addEventListener('click', function (e) {
            e.stopPropagation();

            nav.classList.toggle('mobile-active');
            navToggle.classList.toggle('is-open');

            navToggle.setAttribute(
                'aria-expanded',
                nav.classList.contains('mobile-active')
            );
        });

        document.addEventListener('click', function (e) {
            if (
                nav.classList.contains('mobile-active') &&
                !nav.contains(e.target) &&
                !navToggle.contains(e.target)
            ) {
                nav.classList.remove('mobile-active');
                navToggle.classList.remove('is-open');
                navToggle.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // Sales Navigation Dropdown
    const salesTriggers = document.querySelectorAll(
        '[data-sales-nav-trigger]'
    );

    salesTriggers.forEach(trigger => {
        trigger.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();

            const parent = trigger.closest('[data-sales-nav-item]');

            if (!parent) return;

            parent.classList.toggle('is-open');

            const expanded =
                trigger.getAttribute('aria-expanded') === 'true';

            trigger.setAttribute(
                'aria-expanded',
                expanded ? 'false' : 'true'
            );
        });
    });

});
