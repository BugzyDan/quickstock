(function () {
    const shell = window.QuickStockShell = window.QuickStockShell || {};

    const bindUserMenu = () => {
        const menuBtn = document.getElementById("userMenuBtn");
        const dropdown = document.getElementById("userDropdown");
        if (!menuBtn || !dropdown || menuBtn.dataset.dropdownBound === "true") return;

        menuBtn.dataset.dropdownBound = "true";

        const isMobileLayout = () => window.innerWidth <= 900;

        const positionDropdown = () => {
            if (dropdown.style.display !== "block") return;

            if (!isMobileLayout()) {
                dropdown.style.position = "";
                dropdown.style.top = "";
                dropdown.style.left = "";
                dropdown.style.right = "";
                dropdown.style.maxWidth = "";
                return;
            }

            const rect = menuBtn.getBoundingClientRect();
            const viewportPadding = 12;
            const maxWidth = Math.max(180, window.innerWidth - (viewportPadding * 2));

            dropdown.style.position = "fixed";
            dropdown.style.left = `${viewportPadding}px`;
            dropdown.style.right = "auto";
            dropdown.style.top = `${Math.round(rect.bottom + 10)}px`;
            dropdown.style.maxWidth = `${maxWidth}px`;

            const dropdownWidth = Math.min(dropdown.offsetWidth || maxWidth, maxWidth);
            const alignedLeft = Math.min(
                Math.max(viewportPadding, rect.right - dropdownWidth),
                window.innerWidth - dropdownWidth - viewportPadding
            );
            const maxTop = Math.max(viewportPadding, window.innerHeight - dropdown.offsetHeight - viewportPadding);

            dropdown.style.left = `${Math.round(alignedLeft)}px`;
            dropdown.style.top = `${Math.round(Math.min(rect.bottom + 10, maxTop))}px`;
        };

        const setOpen = (isOpen) => {
            dropdown.style.display = isOpen ? "block" : "none";
            document.body.classList.toggle("qs-operator-menu-open", isOpen);
            if (isOpen) {
                positionDropdown();
            }
            menuBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
        };

        menuBtn.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            setOpen(dropdown.style.display !== "block");
        }, true);

        document.addEventListener("click", function (event) {
            if (!dropdown.contains(event.target) && !menuBtn.contains(event.target)) {
                setOpen(false);
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                setOpen(false);
            }
        });

        window.addEventListener("resize", positionDropdown);
        window.addEventListener("scroll", positionDropdown, true);
    };

    const placeStarterBanner = () => {
        const banner = document.querySelector("[data-starter-plan-banner]");
        if (!banner) return;

        const nav = document.getElementById("main-nav");
        if (nav) {
            nav.insertAdjacentElement("afterend", banner);
        }
    };

    const syncNavPosition = () => {
        const header = document.querySelector("header");
        const nav = document.getElementById("main-nav");
        if (!header || !nav) return;

        const headerRect = header.getBoundingClientRect();
        const headerHeight = Math.ceil(headerRect.height);
        const navTop = Math.max(0, Math.ceil(headerRect.bottom));
        document.documentElement.style.setProperty("--qs-header-height", `${headerHeight}px`);
        document.documentElement.style.setProperty("--qs-mobile-nav-top", `${navTop}px`);
        nav.style.top = window.innerWidth <= 900 ? "" : `${headerHeight}px`;
    };

    const bindMobileNav = () => {
        const navToggle = document.getElementById("navToggle");
        const nav = document.getElementById("main-nav");
        const header = document.querySelector("header");
        if (!navToggle || !nav || navToggle.dataset.mobileNavBound === "true") return;

        navToggle.dataset.mobileNavBound = "true";
        document.documentElement.dataset.qsShellBound = "true";

        const setOpen = (isOpen) => {
            nav.classList.toggle("mobile-active", isOpen);
            navToggle.classList.toggle("is-open", isOpen);
            navToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
            nav.setAttribute("aria-hidden", isOpen ? "false" : "true");
            document.body.classList.toggle("qs-mobile-nav-open", isOpen);
        };

        navToggle.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            syncNavPosition();
            setOpen(!nav.classList.contains("mobile-active"));
        }, true);

        document.addEventListener("click", function (event) {
            if (
                nav.classList.contains("mobile-active") &&
                !nav.contains(event.target) &&
                !navToggle.contains(event.target)
            ) {
                setOpen(false);
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") setOpen(false);
        });

        window.addEventListener("resize", function () {
            syncNavPosition();
            if (window.innerWidth > 900) setOpen(false);
        });
        window.addEventListener("load", syncNavPosition);

        if (header && "MutationObserver" in window) {
            const observer = new MutationObserver(syncNavPosition);
            observer.observe(header, { attributes: true, childList: true, subtree: true });
        }

        setOpen(false);
        syncNavPosition();
    };

    shell.bindUserMenu = bindUserMenu;
    shell.placeStarterBanner = placeStarterBanner;
    shell.bindMobileNav = bindMobileNav;
    shell.syncNavPosition = syncNavPosition;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            bindUserMenu();
            placeStarterBanner();
            bindMobileNav();
        }, { once: true });
    } else {
        bindUserMenu();
        placeStarterBanner();
        bindMobileNav();
    }
})();
