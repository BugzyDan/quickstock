document.addEventListener('DOMContentLoaded', function() {
    // User Dropdown Toggle
    const menuBtn = document.getElementById('userMenuBtn');
    const dropdown = document.getElementById('userDropdown');
    if (menuBtn && dropdown) {
        menuBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block';
        });
        window.addEventListener('click', function(e) {
            if (!menuBtn.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.style.display = 'none';
            }
        });
    }

    // Mobile Nav Toggle
    const navToggle = document.getElementById('navToggle');
    const nav = document.getElementById('main-nav');
    if (navToggle && nav && document.documentElement.dataset.qsShellBound !== 'true') {
        navToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            nav.classList.toggle('mobile-active');
            navToggle.classList.toggle('is-open');
            navToggle.setAttribute('aria-expanded', nav.classList.contains('mobile-active'));
        });
        document.addEventListener('click', function(e) {
            if (nav.classList.contains('mobile-active') && !nav.contains(e.target) && !navToggle.contains(e.target)) {
                nav.classList.remove('mobile-active');
                navToggle.classList.remove('is-open');
                navToggle.setAttribute('aria-expanded', 'false');
            }
        });
    }
});
