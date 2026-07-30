document.addEventListener("DOMContentLoaded", function () {
    const wrapper = document.querySelector("main.sales-page-main");
    const form = document.getElementById("salesInvoiceForm");
    const sourceInput = document.getElementById("invoiceSourceMode");
    const modeButtons = Array.from(document.querySelectorAll(".invoice-mode-btn"));
    const quotePanel = document.getElementById("invoiceFromQuotationPanel");
    const scratchPanel = document.getElementById("invoiceFromScratchPanel");

    const quotationSelect = document.getElementById("quotationSelect");
    const quoteLocationSelect = document.getElementById("quoteLocationSelect");
    const scratchCustomerSelect = document.getElementById("scratchCustomerSelect");
    const scratchLocationSelect = document.getElementById("scratchLocationSelect");
    const scratchNotes = document.getElementById("scratchNotes");

    const rowTemplate = document.getElementById("invoiceLineRowTemplate");
    const lineBody = document.getElementById("invoiceLineBody");
    const addBtn = document.getElementById("addInvoiceLine");
    const subtotalNode = document.getElementById("invoiceSubtotal");
    const discountNode = document.getElementById("invoiceDiscount");
    const taxNode = document.getElementById("invoiceTax");
    const totalNode = document.getElementById("invoiceTotal");
    const taxLabelNode = document.getElementById("invoiceTaxLabel");
    const taxModeSelect = document.getElementById("invoiceTaxMode");
    const taxRateInput = document.getElementById("invoiceTaxRateInput");
    const discountTypeSelect = document.getElementById("invoiceDiscountType");
    const discountValueInput = document.getElementById("invoiceDiscountValue");

    if (!wrapper || !form || !sourceInput || !quotePanel || !scratchPanel) return;

    const taxRatePercent = Number(wrapper.getAttribute("data-tax-rate") || "15");
    const taxRate = Number.isFinite(taxRatePercent) ? (taxRatePercent / 100) : 0.15;

    const money = (value) => {
        const n = Number(value || 0);
        return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    };

    const updateTotals = () => {
        if (!lineBody || !subtotalNode || !taxNode || !totalNode) return;
        let subtotal = 0;
        let taxableSubtotal = 0;

        lineBody.querySelectorAll(".invoice-line-row").forEach((row) => {
            const itemSelect = row.querySelector(".invoice-line-item");
            const qtyInput = row.querySelector(".invoice-line-qty");
            const priceInput = row.querySelector(".invoice-line-price");
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

        const selectedCustomer = scratchCustomerSelect ? scratchCustomerSelect.options[scratchCustomerSelect.selectedIndex] : null;
        const customerTaxExempt = selectedCustomer?.getAttribute("data-tax-exempt") === "1";
        const taxMode = customerTaxExempt ? "none" : (taxModeSelect ? taxModeSelect.value : "default");
        let activeTaxRate = taxRate;
        let taxLabel = `Tax (${taxRatePercent}%)`;
        if (customerTaxExempt) {
            activeTaxRate = 0;
            taxLabel = "GCT Exempt";
        } else if (taxMode === "none") {
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
        const itemSelect = row.querySelector(".invoice-line-item");
        const qtyInput = row.querySelector(".invoice-line-qty");
        const priceInput = row.querySelector(".invoice-line-price");
        const stockInput = row.querySelector(".invoice-line-stock");
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
            if (!lineBody.querySelector(".invoice-line-row")) {
                addRow();
            }
            updateTotals();
        });
        syncFromItem();
    };

    const addRow = () => {
        if (!rowTemplate || !lineBody) return;
        const fragment = rowTemplate.content.cloneNode(true);
        const row = fragment.querySelector(".invoice-line-row");
        lineBody.appendChild(fragment);
        if (row) bindRow(row);
    };

    const setFieldState = (element, disabled, required) => {
        if (!element) return;
        element.disabled = !!disabled;
        if (required) element.setAttribute("required", "required");
        else element.removeAttribute("required");
    };

    const syncModeState = () => {
        const mode = sourceInput.value === "quotation" ? "quotation" : "scratch";
        const quotationMode = mode === "quotation";

        quotePanel.style.display = quotationMode ? "block" : "none";
        scratchPanel.style.display = quotationMode ? "none" : "block";

        setFieldState(quotationSelect, !quotationMode, quotationMode);
        setFieldState(quoteLocationSelect, !quotationMode, quotationMode);

        setFieldState(scratchCustomerSelect, quotationMode, false);
        setFieldState(scratchLocationSelect, quotationMode, !quotationMode);
        setFieldState(scratchNotes, quotationMode, false);
        setFieldState(taxModeSelect, quotationMode, false);
        setFieldState(taxRateInput, quotationMode, false);
        setFieldState(discountTypeSelect, quotationMode, false);
        setFieldState(discountValueInput, quotationMode, false);

        if (lineBody) {
            lineBody.querySelectorAll(".invoice-line-row").forEach((row) => {
                const item = row.querySelector(".invoice-line-item");
                const qty = row.querySelector(".invoice-line-qty");
                const price = row.querySelector(".invoice-line-price");
                setFieldState(item, quotationMode, !quotationMode);
                setFieldState(qty, quotationMode, !quotationMode);
                setFieldState(price, quotationMode, !quotationMode);
            });
        }

        modeButtons.forEach((btn) => {
            const active = btn.getAttribute("data-source") === mode;
            btn.style.background = active ? "#0f172a" : "#334155";
        });
        updateTotals();
    };

    modeButtons.forEach((btn) => {
        btn.addEventListener("click", function () {
            const mode = btn.getAttribute("data-source");
            sourceInput.value = mode === "quotation" ? "quotation" : "scratch";
            syncModeState();
        });
    });

    if (addBtn) addBtn.addEventListener("click", addRow);
    if (taxModeSelect) taxModeSelect.addEventListener("change", updateTotals);
    if (taxRateInput) taxRateInput.addEventListener("input", updateTotals);
    if (scratchCustomerSelect) {
        scratchCustomerSelect.addEventListener("change", () => {
            const selected = scratchCustomerSelect.options[scratchCustomerSelect.selectedIndex];
            const exempt = selected?.getAttribute("data-tax-exempt") === "1";
            if (taxModeSelect && exempt) taxModeSelect.value = "none";
            updateTotals();
        });
    }
    if (discountTypeSelect) discountTypeSelect.addEventListener("change", updateTotals);
    if (discountValueInput) discountValueInput.addEventListener("input", updateTotals);
    if (lineBody && !lineBody.querySelector(".invoice-line-row")) addRow();

    syncModeState();
});
