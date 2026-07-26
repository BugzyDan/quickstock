document.addEventListener("DOMContentLoaded", () => {
    const menuBtn = document.getElementById("userMenuBtn");
    const dropdown = document.getElementById("userDropdown");
    const navToggle = document.getElementById("navToggle");
    const nav = document.getElementById("main-nav");
    const isMobileNav = () => window.innerWidth <= 900;

    const closeNav = () => {
        if (!nav || !navToggle) return;
        nav.classList.remove("mobile-active");
        nav.setAttribute("aria-hidden", "true");
        navToggle.classList.remove("is-open");
        navToggle.setAttribute("aria-expanded", "false");
    };

    if (menuBtn && dropdown) {
        menuBtn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();

            const isHidden = window.getComputedStyle(dropdown).display === "none";
            dropdown.style.display = isHidden ? "block" : "none";
        });
    }

    if (nav && navToggle && document.documentElement.dataset.qsShellBound !== "true") {
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();

            const isOpen = nav.classList.toggle("mobile-active");
            nav.setAttribute("aria-hidden", isOpen ? "false" : "true");
            navToggle.classList.toggle("is-open", isOpen);
            navToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });

        nav.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", closeNav);
        });
    }

    document.addEventListener("click", (event) => {
        if (dropdown && dropdown.style.display === "block" && !dropdown.contains(event.target) && !menuBtn?.contains(event.target)) {
            dropdown.style.display = "none";
        }

        if (
            nav &&
            navToggle &&
            isMobileNav() &&
            nav.classList.contains("mobile-active") &&
            !nav.contains(event.target) &&
            !navToggle.contains(event.target)
        ) {
            closeNav();
        }
    });

    window.addEventListener("resize", () => {
        if (!isMobileNav()) {
            closeNav();
        }
    });
});
