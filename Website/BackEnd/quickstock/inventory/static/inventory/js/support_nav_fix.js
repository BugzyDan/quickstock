document.addEventListener('DOMContentLoaded', () => {
  const nav = document.querySelector('nav.qs-support-nav') || document.getElementById('main-nav');
  const navToggle = document.getElementById('navToggle');
  if (!nav || !navToggle) return;

  // Gate: only run for pages that explicitly opt-in.
  if (nav.dataset.qsSupportNavToggle !== 'true' && window.__qsSupportNavToggleTarget !== 'true') {
    return;
  }

  // Mark as handled so other scripts won't double-toggle.
  if (nav.dataset.qsSupportFixApplied === 'true') return;
  nav.dataset.qsSupportFixApplied = 'true';


  const closeNav = () => {
    nav.classList.remove('mobile-active');
    nav.setAttribute('aria-hidden', 'true');
    navToggle.classList.remove('is-open');
    navToggle.setAttribute('aria-expanded', 'false');
  };

  const openNav = () => {
    nav.classList.add('mobile-active');
    nav.setAttribute('aria-hidden', 'false');
    navToggle.classList.add('is-open');
    navToggle.setAttribute('aria-expanded', 'true');
  };

  const toggleNav = (e) => {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }

    // Only apply on mobile widths.
    if (window.innerWidth > 900) {
      closeNav();
      return;
    }

    const isOpen = nav.classList.contains('mobile-active');
    if (isOpen) closeNav();
    else openNav();
  };

  // Replace any prior shell handler by using a capture-phase click listener.
  navToggle.addEventListener('click', toggleNav, true);

  document.addEventListener('click', (e) => {
    const clickedInsideNav = nav.contains(e.target);
    const clickedToggle = navToggle.contains(e.target);
    if (!clickedInsideNav && !clickedToggle) {
      closeNav();
    }
  }, true);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeNav();
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth > 900) closeNav();
  });

  // Ensure initial state is closed on load for consistency.
  closeNav();
});
