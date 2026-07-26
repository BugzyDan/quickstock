document.addEventListener('DOMContentLoaded', function () {
    const reconForm = document.getElementById('daily-reconciliation-form');
    const reconPanel = document.getElementById('daily-reconciliation-panel');
    const reconInputs = Array.from(document.querySelectorAll('.daily-recon-input'));
    const menuBtn = document.getElementById('userMenuBtn');
    const dropdown = document.getElementById('userDropdown');
    const navToggle = document.getElementById('navToggle');
    const nav = document.getElementById('main-nav');
    const header = document.querySelector('body > header') || document.querySelector('header');

    const syncHeaderHeight = () => {
        if (!header) return;
        document.documentElement.style.setProperty('--qs-header-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
    };

    const closeOperatorMenu = () => {
        if (!menuBtn || !dropdown) return;
        dropdown.style.display = 'none';
        menuBtn.setAttribute('aria-expanded', 'false');
    };

    const closeNav = () => {
        if (!nav || !navToggle) return;
        nav.classList.remove('mobile-active');
        navToggle.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
        nav.setAttribute('aria-hidden', window.innerWidth <= 900 ? 'true' : 'false');
    };

    const restoreReconPosition = () => {
        if (!reconPanel) return;
        if (window.sessionStorage.getItem('qsDailyReconRestore') !== 'true') return;
        window.sessionStorage.removeItem('qsDailyReconRestore');
        window.requestAnimationFrame(function () {
            reconPanel.scrollIntoView({ block: 'start', behavior: 'auto' });
        });
    };

    const focusNextReconInput = (currentInput) => {
        const currentIndex = reconInputs.indexOf(currentInput);
        if (currentIndex < 0) return;
        const nextInput = reconInputs[currentIndex + 1];
        if (nextInput) {
            nextInput.focus();
            nextInput.select?.();
        }
    };

    if (menuBtn && dropdown) {
        menuBtn.addEventListener('click', function (event) {
            event.stopPropagation();
            const isOpen = dropdown.style.display === 'block';
            dropdown.style.display = isOpen ? 'none' : 'block';
            menuBtn.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
        });
    }

    if (navToggle && nav) {
        syncHeaderHeight();
        nav.setAttribute('aria-hidden', window.innerWidth <= 900 ? 'true' : 'false');

        navToggle.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            syncHeaderHeight();
            const isOpen = !nav.classList.contains('mobile-active');
            nav.classList.toggle('mobile-active', isOpen);
            navToggle.classList.toggle('is-open', isOpen);
            navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            nav.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
            window.requestAnimationFrame(syncHeaderHeight);
        });

        nav.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', closeNav);
        });
    }

    if (reconForm && reconInputs.length) {
        reconInputs.forEach(function (input) {
            input.addEventListener('keydown', function (event) {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                focusNextReconInput(input);
            });
        });

        reconForm.addEventListener('submit', function () {
            if (!reconPanel) return;
            window.sessionStorage.setItem('qsDailyReconRestore', 'true');
        });
    }

    document.addEventListener('click', function (event) {
        if (menuBtn && dropdown && !menuBtn.contains(event.target) && !dropdown.contains(event.target)) {
            closeOperatorMenu();
        }

        if (nav && navToggle && nav.classList.contains('mobile-active') && !nav.contains(event.target) && !navToggle.contains(event.target)) {
            closeNav();
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            closeOperatorMenu();
            closeNav();
        }
    });

    window.addEventListener('resize', function () {
        syncHeaderHeight();
        if (window.innerWidth > 900) {
            closeNav();
        } else if (nav && !nav.classList.contains('mobile-active')) {
            nav.setAttribute('aria-hidden', 'true');
        }
    });

    window.addEventListener('load', syncHeaderHeight, { once: true });
    restoreReconPosition();
});
