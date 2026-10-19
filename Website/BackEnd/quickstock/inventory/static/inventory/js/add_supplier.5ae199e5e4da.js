document.addEventListener('DOMContentLoaded', function() {
    const navToggle = document.getElementById('navToggle');
    const nav = document.querySelector('nav');
    const header = document.querySelector('header');

    if (navToggle && !nav) {
        navToggle.hidden = true;
        navToggle.setAttribute('aria-hidden', 'true');
        return;
    }

    const setNavTop = () => {
        if (nav && header) {
            const toggleH = navToggle ? navToggle.getBoundingClientRect().height : 0;
            nav.style.top = `${header.getBoundingClientRect().height + toggleH + 24}px`;
        }
    };
    setNavTop();
    window.addEventListener('resize', setNavTop);
    if (navToggle && nav) {
        navToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            nav.classList.toggle('mobile-active');
            navToggle.classList.toggle('is-open');
        });
        document.addEventListener('click', function(e) {
            if (nav.classList.contains('mobile-active') && !nav.contains(e.target) && e.target !== navToggle) {
                nav.classList.remove('mobile-active');
                navToggle.classList.remove('is-open');
            }
        });
    }
});
