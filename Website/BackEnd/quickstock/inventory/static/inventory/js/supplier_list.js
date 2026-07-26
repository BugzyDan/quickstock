document.addEventListener('DOMContentLoaded', function() {
    const onMediaQueryChange = (mq, handler) => {
        if (mq && typeof mq.addEventListener === 'function') mq.addEventListener('change', handler);
        else if (mq && typeof mq.addListener === 'function') mq.addListener(handler);
    };
    const findNavToggle = () => (
        document.getElementById('navToggle') ||
        document.querySelector('.mobile-nav-toggle') ||
        document.querySelector('.nav-toggle')
    );
    const resolveThemePref = () => (
        document.documentElement.getAttribute('data-theme') ||
        document.documentElement.dataset.theme ||
        document.body?.getAttribute('data-theme') ||
        document.body?.dataset.theme ||
        'system'
    );

    // --------- Navigation Sticky Fix (Eliminates the Gap) ---------
    const nav = document.getElementById('main-nav');
    const header = document.querySelector('header');
    
    const syncNavPosition = () => {
        if (nav && header) {
            const headerHeight = header.offsetHeight;
            document.documentElement.style.setProperty('--qs-header-height', `${headerHeight}px`);
            nav.style.top = `${headerHeight}px`;
        }
    };

    // Run on load and whenever window is resized
    syncNavPosition();
    window.addEventListener('resize', syncNavPosition);
    window.addEventListener('load', syncNavPosition);
    if (header) {
        const observer = new MutationObserver(syncNavPosition);
        observer.observe(header, { attributes: true, childList: true, subtree: true });
    }

    // --------- Mobile Nav Toggle ---------
    const navToggle = findNavToggle();
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

    // --------- User Dropdown ---------
    const menuBtn = document.getElementById('userMenuBtn');
    const dropdown = document.getElementById('userDropdown');

    if(menuBtn && dropdown){
        menuBtn.addEventListener('click', function(e){
            e.stopPropagation();
            const isVisible = dropdown.style.display === 'block';
            dropdown.style.display = isVisible ? 'none' : 'block';
        });

        window.addEventListener('click', function(e){
            if(!menuBtn.contains(e.target) && !dropdown.contains(e.target)){
                dropdown.style.display = 'none';
            }
        });
    }

    // --------- Theme Apply ---------
    // Note: In a JS file, {{ variables }} won't render. 
    // Ensure this value is passed via a data-attribute in the HTML <body> if needed.
    const root = document.documentElement;
    const savedTheme = resolveThemePref();

    function applyTheme(mode){ 
        root.setAttribute('data-theme-applied', mode); 
    }

    if(savedTheme === "system"){
        const mq = window.matchMedia("(prefers-color-scheme: dark)");
        applyTheme(mq.matches ? "dark" : "light");
        onMediaQueryChange(mq, e => applyTheme(e.matches ? "dark" : "light"));
    } else {
        applyTheme(savedTheme);
    }
});
