document.documentElement.classList.add("sales-doc-actions-ready");

document.addEventListener("DOMContentLoaded", () => {
    const lists = Array.from(document.querySelectorAll("[data-sales-document-list]"));

    const csvEscape = (value) => {
        const text = String(value || "");
        return `"${text.replace(/"/g, '""')}"`;
    };

    const closeMenus = (exceptToggle = null) => {
        document.querySelectorAll("[data-sales-menu-toggle]").forEach((toggle) => {
            if (toggle === exceptToggle) return;
            const menu = toggle.closest(".sales-row-action-menu")?.querySelector("[data-sales-menu-panel]");
            toggle.setAttribute("aria-expanded", "false");
            if (menu) menu.hidden = true;
        });
    };

    const openSelectedUrls = (selected, urlKey) => {
        selected.forEach((checkbox) => {
            const url = checkbox.dataset[urlKey];
            if (url) window.open(url, "_blank", "noopener");
        });
    };

    const exportSelectedCsv = (selected, label) => {
        const rows = [
            ["Type", "Document #", "Customer", "Status", "Total", "Open URL"],
            ...selected.map((checkbox) => [
                checkbox.dataset.docType || label,
                checkbox.dataset.docNumber,
                checkbox.dataset.customer,
                checkbox.dataset.status,
                checkbox.dataset.total,
                checkbox.dataset.openUrl,
            ]),
        ];
        const csv = rows.map((row) => row.map(csvEscape).join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `quickstock-${label || "sales-documents"}-selected.csv`;
        document.body.appendChild(link);
        link.click();
        URL.revokeObjectURL(link.href);
        link.remove();
    };

    document.addEventListener("click", (event) => {
        const toggle = event.target.closest("[data-sales-menu-toggle]");
        if (toggle) {
            event.preventDefault();
            const menu = toggle.closest(".sales-row-action-menu")?.querySelector("[data-sales-menu-panel]");
            const willOpen = menu?.hidden;
            closeMenus(toggle);
            toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
            if (menu) menu.hidden = !willOpen;
            return;
        }

        const printButton = event.target.closest("[data-sales-print-one]");
        if (printButton) {
            event.preventDefault();
            const url = printButton.dataset.printUrl;
            if (url) window.open(url, "_blank", "noopener");
            closeMenus();
            return;
        }

        if (!event.target.closest(".sales-row-action-menu")) {
            closeMenus();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeMenus();
    });

    lists.forEach((list) => {
        const selectAll = list.querySelector("[data-sales-select-all]");
        const checkboxes = Array.from(list.querySelectorAll("[data-sales-row-select]"));
        const bulkBar = list.querySelector("[data-sales-bulk-bar]");
        const selectedCount = list.querySelector("[data-sales-selected-count]");
        const documentLabel = list.dataset.documentLabel || "sales-documents";

        const selectedRows = () => checkboxes.filter((checkbox) => checkbox.checked);

        const syncSelectionUi = () => {
            const selected = selectedRows();
            if (bulkBar) bulkBar.hidden = selected.length === 0;
            if (selectedCount) selectedCount.textContent = String(selected.length);
            if (selectAll) {
                selectAll.checked = selected.length > 0 && selected.length === checkboxes.length;
                selectAll.indeterminate = selected.length > 0 && selected.length < checkboxes.length;
            }
        };

        selectAll?.addEventListener("change", () => {
            checkboxes.forEach((checkbox) => {
                checkbox.checked = selectAll.checked;
            });
            syncSelectionUi();
        });

        checkboxes.forEach((checkbox) => {
            checkbox.addEventListener("change", syncSelectionUi);
        });

        list.querySelectorAll("[data-sales-bulk-action]").forEach((button) => {
            button.addEventListener("click", () => {
                const selected = selectedRows();
                if (!selected.length) return;

                if (button.dataset.salesBulkAction === "email") {
                    openSelectedUrls(selected, "emailUrl");
                } else if (button.dataset.salesBulkAction === "print") {
                    openSelectedUrls(selected, "printUrl");
                } else if (button.dataset.salesBulkAction === "csv") {
                    exportSelectedCsv(selected, documentLabel);
                }
            });
        });

        syncSelectionUi();
    });

    closeMenus();
});
