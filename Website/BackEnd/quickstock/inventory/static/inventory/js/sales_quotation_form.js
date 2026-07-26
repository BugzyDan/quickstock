document.addEventListener("DOMContentLoaded", function () {
    const wrapper = document.querySelector("main.sales-page-main");
    const rowTemplate = document.getElementById("quoteLineRowTemplate");
    const lineBody = document.getElementById("quoteLineBody");
    const addBtn = document.getElementById("addQuoteLine");
    const subtotalNode = document.getElementById("quoteSubtotal");
    const discountNode = document.getElementById("quoteDiscount");
    const taxNode = document.getElementById("quoteTax");
    const totalNode = document.getElementById("quoteTotal");
    const taxLabelNode = document.getElementById("quoteTaxLabel");
    const taxModeSelect = document.getElementById("quoteTaxMode");
    const taxRateInput = document.getElementById("quoteTaxRateInput");
    const discountTypeSelect = document.getElementById("quoteDiscountType");
    const discountValueInput = document.getElementById("quoteDiscountValue");

    if (!wrapper || !rowTemplate || !lineBody || !addBtn) return;

    const taxRatePercent = Number(wrapper.getAttribute("data-tax-rate") || "15");
    const taxRate = Number.isFinite(taxRatePercent) ? (taxRatePercent / 100) : 0.15;

    const money = (value) => {
        const n = Number(value || 0);
        return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    };

    const updateTotals = () => {
        let subtotal = 0;
        let taxableSubtotal = 0;

        lineBody.querySelectorAll(".quote-line-row").forEach((row) => {
            const itemSelect = row.querySelector(".quote-line-item");
            const qtyInput = row.querySelector(".quote-line-qty");
            const priceInput = row.querySelector(".quote-line-price");
            if (!itemSelect || !qtyInput || !priceInput) return;

            const selected = itemSelect.options[itemSelect.selectedIndex];
            const qty = Math.max(0, parseInt(qtyInput.value || "0", 10) || 0);
            const price = Math.max(0, parseFloat(priceInput.value || "0") || 0);
            const lineTotal = qty * price;
            subtotal += lineTotal;

            if (selected && selected.getAttribute("data-taxable") === "1") {
                taxableSubtotal += lineTotal;
            }
        });

        const discountType = discountTypeSelect ? discountTypeSelect.value : "flat";
        const discountValue = Math.max(0, parseFloat(discountValueInput?.value || "0") || 0);
        let discountAmount = discountType === "percent" ? subtotal * (discountValue / 100) : discountValue;
        discountAmount = Math.max(0, Math.min(discountAmount, subtotal));

        const taxableRatio = subtotal > 0 ? (taxableSubtotal / subtotal) : 0;
        const taxableAfterDiscount = Math.max(0, taxableSubtotal - (discountAmount * taxableRatio));

        const taxMode = taxModeSelect ? taxModeSelect.value : "default";
        let activeTaxRate = taxRate;
        let taxLabel = `Tax (${taxRatePercent}%)`;
        if (taxMode === "none") {
            activeTaxRate = 0;
            taxLabel = "Tax Removed";
        } else if (taxMode === "custom") {
            const customPercent = Math.max(0, parseFloat(taxRateInput?.value || "0") || 0);
            activeTaxRate = customPercent / 100;
            taxLabel = `Tax (${customPercent.toFixed(2)}%)`;
        }

        const tax = taxableAfterDiscount * activeTaxRate;
        const total = subtotal - discountAmount + tax;
        subtotalNode.textContent = money(subtotal);
        if (discountNode) discountNode.textContent = money(discountAmount);
        if (taxLabelNode) taxLabelNode.textContent = taxLabel;
        taxNode.textContent = money(tax);
        totalNode.textContent = money(total);
    };

    const bindRow = (row) => {
        const itemSelect = row.querySelector(".quote-line-item");
        const qtyInput = row.querySelector(".quote-line-qty");
        const priceInput = row.querySelector(".quote-line-price");
        const stockInput = row.querySelector(".quote-line-stock");
        const removeBtn = row.querySelector(".quote-line-remove");

        const syncFromItem = () => {
            const selected = itemSelect.options[itemSelect.selectedIndex];
            if (!selected || !selected.value) {
                stockInput.value = "0";
                priceInput.value = "0.00";
                updateTotals();
                return;
            }

            const stock = parseInt(selected.getAttribute("data-stock") || "0", 10) || 0;
            const price = parseFloat(selected.getAttribute("data-price") || "0") || 0;

            stockInput.value = String(stock);
            qtyInput.max = String(Math.max(stock, 1));
            if (!priceInput.value || Number(priceInput.value) <= 0) {
                priceInput.value = price.toFixed(2);
            }
            updateTotals();
        };

        itemSelect.addEventListener("change", syncFromItem);
        qtyInput.addEventListener("input", updateTotals);
        priceInput.addEventListener("input", updateTotals);

        removeBtn.addEventListener("click", function () {
            row.remove();
            if (!lineBody.querySelector(".quote-line-row")) {
                addRow();
            }
            updateTotals();
        });

        syncFromItem();
    };

    const addRow = () => {
        const fragment = rowTemplate.content.cloneNode(true);
        const row = fragment.querySelector(".quote-line-row");
        lineBody.appendChild(fragment);
        if (row) bindRow(row);
    };

    addBtn.addEventListener("click", addRow);
    if (taxModeSelect) taxModeSelect.addEventListener("change", updateTotals);
    if (taxRateInput) taxRateInput.addEventListener("input", updateTotals);
    if (discountTypeSelect) discountTypeSelect.addEventListener("change", updateTotals);
    if (discountValueInput) discountValueInput.addEventListener("input", updateTotals);
    addRow();
    updateTotals();
});
