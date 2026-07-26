document.addEventListener('DOMContentLoaded', function () {
    const menuBtn = document.getElementById('userMenuBtn');
    const dropdown = document.getElementById('userDropdown');
    const navToggle = document.getElementById('navToggle');
    const nav = document.getElementById('main-nav');

    if (menuBtn && dropdown && menuBtn.dataset.customerListMenuBound !== 'true') {
        menuBtn.dataset.customerListMenuBound = 'true';
        menuBtn.addEventListener('click', function (event) {
            event.stopPropagation();
            const isOpen = dropdown.style.display === 'block';
            dropdown.style.display = isOpen ? 'none' : 'block';
            menuBtn.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
        });
    }

    if (navToggle && nav && navToggle.dataset.customerListNavBound !== 'true') {
        navToggle.dataset.customerListNavBound = 'true';
        navToggle.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            const isOpen = !nav.classList.contains('mobile-active');
            nav.classList.toggle('mobile-active', isOpen);
            navToggle.classList.toggle('is-open', isOpen);
            navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            nav.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
        });
    }

    document.addEventListener('click', function (event) {
        if (dropdown && menuBtn && !dropdown.contains(event.target) && !menuBtn.contains(event.target)) {
            dropdown.style.display = 'none';
            menuBtn.setAttribute('aria-expanded', 'false');
        }

        if (nav && navToggle && nav.classList.contains('mobile-active') && !nav.contains(event.target) && !navToggle.contains(event.target)) {
            nav.classList.remove('mobile-active');
            navToggle.classList.remove('is-open');
            navToggle.setAttribute('aria-expanded', 'false');
            nav.setAttribute('aria-hidden', 'true');
        }
    });

    window.addEventListener('resize', function () {
        if (window.innerWidth > 900 && nav && navToggle) {
            nav.classList.remove('mobile-active');
            navToggle.classList.remove('is-open');
            navToggle.setAttribute('aria-expanded', 'false');
            nav.setAttribute('aria-hidden', 'false');
        }
    });
});
