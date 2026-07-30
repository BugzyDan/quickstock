(function () {
    function setCategoryState(button, expanded) {
        var panelId = button.getAttribute("aria-controls");
        var panel = panelId ? document.getElementById(panelId) : null;
        var label = button.querySelector(".inventory-overview-toggle-text");

        button.setAttribute("aria-expanded", expanded ? "true" : "false");
        if (label) {
            label.textContent = expanded ? "Hide Items" : "Show Items";
        }
        if (panel) {
            panel.hidden = !expanded;
            panel.classList.toggle("is-collapsed", !expanded);
        }
    }

    function initCategoryToggles() {
        var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-category-toggle]"));
        if (!buttons.length) {
            return;
        }

        buttons.forEach(function (button) {
            button.addEventListener("click", function () {
                setCategoryState(button, button.getAttribute("aria-expanded") !== "true");
            });
        });

        document.querySelectorAll("[data-category-action]").forEach(function (control) {
            control.addEventListener("click", function () {
                var shouldExpand = control.getAttribute("data-category-action") === "expand";
                buttons.forEach(function (button) {
                    setCategoryState(button, shouldExpand);
                });
            });
        });
    }

    function initQuickNav() {
        var chips = Array.prototype.slice.call(document.querySelectorAll(".inventory-overview-nav-chip"));
        if (!chips.length) {
            return;
        }

        function setActiveChip(activeChip) {
            chips.forEach(function (chip) {
                chip.classList.toggle("is-active", chip === activeChip);
            });
        }

        chips.forEach(function (chip) {
            chip.addEventListener("click", function () {
                setActiveChip(chip);
            });
        });

        var sections = chips
            .map(function (chip) {
                var id = chip.getAttribute("href");
                return id && id.charAt(0) === "#" ? document.querySelector(id) : null;
            })
            .filter(Boolean);

        if (!("IntersectionObserver" in window) || !sections.length) {
            return;
        }

        var observer = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting) {
                        return;
                    }
                    var activeChip = chips.find(function (chip) {
                        return chip.getAttribute("href") === "#" + entry.target.id;
                    });
                    if (activeChip) {
                        setActiveChip(activeChip);
                    }
                });
            },
            { rootMargin: "-20% 0px -65% 0px", threshold: 0.01 }
        );

        sections.forEach(function (section) {
            observer.observe(section);
        });
    }

    function initInventoryOverview() {
        initCategoryToggles();
        initQuickNav();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initInventoryOverview);
    } else {
        initInventoryOverview();
    }
})();
