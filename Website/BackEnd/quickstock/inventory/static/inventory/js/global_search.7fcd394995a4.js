document.addEventListener("DOMContentLoaded", function () {
    const onMediaQueryChange = (mq, handler) => {
        if (mq && typeof mq.addEventListener === "function") mq.addEventListener("change", handler);
        else if (mq && typeof mq.addListener === "function") mq.addListener(handler);
    };

    const findNavToggle = () => (
        document.getElementById("navToggle") ||
        document.querySelector(".mobile-nav-toggle") ||
        document.querySelector(".nav-toggle")
    );

    const resolveThemePref = () => (
        document.documentElement.getAttribute("data-theme") ||
        document.documentElement.dataset.theme ||
        document.body?.getAttribute("data-theme") ||
        document.body?.dataset.theme ||
        "system"
    );

    const applyTheme = (mode) => {
        document.documentElement.setAttribute("data-theme-applied", mode);
    };

    const savedTheme = resolveThemePref();
    if (savedTheme === "system") {
        const mq = window.matchMedia("(prefers-color-scheme: dark)");
        applyTheme(mq.matches ? "dark" : "light");
        onMediaQueryChange(mq, (e) => applyTheme(e.matches ? "dark" : "light"));
    } else {
        applyTheme(savedTheme);
    }

    const nav = document.getElementById("main-nav");
    const header = document.querySelector("header");

    const syncNavPosition = () => {
        if (nav && header) {
            const headerHeight = header.offsetHeight;
            document.documentElement.style.setProperty("--qs-header-height", `${headerHeight}px`);
            nav.style.top = `${headerHeight}px`;
        }
    };

    syncNavPosition();
    window.addEventListener("resize", syncNavPosition);
    window.addEventListener("load", syncNavPosition);
    if (header) {
        const observer = new MutationObserver(syncNavPosition);
        observer.observe(header, { attributes: true, childList: true, subtree: true });
    }

    const navToggle = findNavToggle();
    if (navToggle && nav && document.documentElement.dataset.qsShellBound !== 'true') {
        navToggle.addEventListener("click", (e) => {
            e.stopPropagation();
            nav.classList.toggle("mobile-active");
            navToggle.classList.toggle("is-open");
        });

        document.addEventListener("click", (e) => {
            if (nav.classList.contains("mobile-active") && !nav.contains(e.target) && !navToggle.contains(e.target)) {
                nav.classList.remove("mobile-active");
                navToggle.classList.remove("is-open");
            }
        });
    }

    const menuBtn = document.getElementById("userMenuBtn");
    const dropdown = document.getElementById("userDropdown");
    if (menuBtn && dropdown) {
        menuBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            const isVisible = dropdown.style.display === "block";
            dropdown.style.display = isVisible ? "none" : "block";
        });

        window.addEventListener("click", function (e) {
            if (!menuBtn.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.style.display = "none";
            }
        });
    }
});
