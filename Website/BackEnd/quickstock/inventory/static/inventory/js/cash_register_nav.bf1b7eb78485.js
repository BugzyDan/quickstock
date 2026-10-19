    document.addEventListener('DOMContentLoaded', function() {
        const navToggle = document.getElementById('navToggle');
        const nav = document.getElementById('cash-mobile-nav') || document.getElementById('main-nav');
        const header = document.querySelector('header');
        const setNavTop = () => {
            if (nav && header) {
                const headerHeight = Math.ceil(header.getBoundingClientRect().height);
                document.documentElement.style.setProperty('--cash-header-height', `${headerHeight}px`);
                nav.style.top = window.innerWidth <= 900 ? `${headerHeight}px` : '';
            }
        };
        setNavTop();
        window.addEventListener('resize', setNavTop);

        if (navToggle && nav && document.documentElement.dataset.qsShellBound !== 'true') {
            navToggle.dataset.mobileNavBound = 'true';

        const setOpen = (isOpen) => {
            nav.classList.toggle('mobile-active', isOpen);
            navToggle.classList.toggle('is-open', isOpen);
            navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            nav.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
            document.body.classList.toggle('qs-mobile-nav-open', isOpen);
            if (isOpen) {
                const searchOverlay = document.getElementById('cashSearchOverlay');
                const searchToggle = document.getElementById('cashMobileSearchToggle');
                document.body.classList.remove('cash-search-open');
                if (searchOverlay) searchOverlay.hidden = true;
                if (searchToggle) searchToggle.setAttribute('aria-expanded', 'false');
            }
        };

            navToggle.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                setNavTop();
                setOpen(!nav.classList.contains('mobile-active'));
            });
            document.addEventListener('click', function(e) {
                if (nav.classList.contains('mobile-active') && !nav.contains(e.target) && !navToggle.contains(e.target)) {
                    setOpen(false);
                }
            });
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') setOpen(false);
            });
            window.addEventListener('resize', function() {
                if (window.innerWidth > 900) setOpen(false);
            });

            setOpen(false);
        }
    });
