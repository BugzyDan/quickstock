document.addEventListener('DOMContentLoaded', function () {

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
