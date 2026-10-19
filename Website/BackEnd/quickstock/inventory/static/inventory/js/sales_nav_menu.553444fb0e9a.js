document.addEventListener("DOMContentLoaded", function () {
    // Prevent duplicated caret rendering when the same script is loaded more than once.
    // The caret itself is already rendered in the template as <span class="sales-nav-caret">⌄</span>.
    // If any other JS/styles injected additional caret nodes, remove them idempotently.
    // If your template/JS injects duplicate caret nodes with a marker class, remove them.
    // (Keeps original caret span in `app_shell_nav.html` intact.)
    document.querySelectorAll(".sales-nav-item .sales-nav-caret--injected").forEach(n => n.remove());

    const menuItems = Array.from(document.querySelectorAll("[data-sales-nav-item]"));
    if (!menuItems.length) return;

    const isMobileNav = () => window.innerWidth <= 900;
    const isDesktopNav = () => !isMobileNav();

    const setExpanded = (item, expanded) => {
        const trigger = item.querySelector("[data-sales-nav-trigger]");
        if (trigger) {
            trigger.setAttribute("aria-expanded", expanded ? "true" : "false");
        }
        item.dataset.salesNavExpanded = expanded ? "true" : "false";
    };

    const closeItem = (item) => {
        item.classList.remove("open");
        item.classList.remove("is-open");
        const menu = item.querySelector("[data-sales-nav-menu]");
        if (menu) {
            menu.classList.remove("show");
        }
        setExpanded(item, false);
    };

    const openItem = (item) => {
        item.classList.add("open");
        item.classList.add("is-open");
        const menu = item.querySelector("[data-sales-nav-menu]");
        if (menu) {
            menu.classList.add("show");
        }
        setExpanded(item, true);
    };

    const closeAll = (exceptItem) => {
        menuItems.forEach((item) => {
            if (exceptItem && item === exceptItem) return;
            closeItem(item);
        });
    };

    menuItems.forEach((item) => {
        if (item.dataset.salesNavMenuBound === "true") return;
        item.dataset.salesNavMenuBound = "true";

        const trigger = item.querySelector("[data-sales-nav-trigger]");
        if (!trigger) return;

        trigger.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            const willOpen = !item.classList.contains("open") && !item.classList.contains("is-open");
            closeAll(item);
            if (willOpen) {
                openItem(item);
            } else {
                closeItem(item);
            }
        });

        item.addEventListener("mouseenter", function () {
            if (!isDesktopNav()) return;
            closeAll(item);
            openItem(item);
        });

        item.addEventListener("mouseleave", function () {
            if (!isDesktopNav()) return;
            closeItem(item);
        });

        trigger.addEventListener("focus", function () {
            if (!isDesktopNav()) return;
            closeAll(item);
            openItem(item);
        });
    });

    document.addEventListener("click", function (event) {
        const clickedInside = menuItems.some((item) => item.contains(event.target));
        if (!clickedInside) {
            closeAll();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeAll();
        }
    });

    const applyResponsiveDefaults = () => {
        if (isMobileNav()) {
            menuItems.forEach((item) => {
                if (item.classList.contains("is-active")) {
                    openItem(item);
                } else {
                    closeItem(item);
                }
            });
        } else {
            closeAll();
        }
    };

    applyResponsiveDefaults();
    window.addEventListener("resize", applyResponsiveDefaults);
});
