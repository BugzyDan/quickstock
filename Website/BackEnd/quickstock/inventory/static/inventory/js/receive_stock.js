document.addEventListener("DOMContentLoaded", () => {
    const barcodeInput = document.getElementById("stock-barcode-input");
    const itemSelect = document.getElementById("receive-item-select") || document.querySelector('select[name="item"]');
    const locationSelect = document.getElementById("receive-location-select");
    const qtyInput = document.getElementById("receive-qty-input");
    const costInput = document.getElementById("receive-cost-input");
    const liveItem = document.getElementById("receive_live_item");
    const liveCode = document.getElementById("receive_live_code");
    const liveLocation = document.getElementById("receive_live_location");
    const liveMath = document.getElementById("receive_live_math");
    const batchTotal = document.getElementById("batch_total");
    const itemFoundPanel = document.getElementById("receive_item_found_panel");
    const itemFoundStatus = document.getElementById("receive_item_found_status");
    const itemFoundTitle = document.getElementById("receive_item_found_title");
    const itemFoundName = document.getElementById("receive_item_found_name");
    const itemCurrentStock = document.getElementById("receive_item_current_stock");
    const itemLastCost = document.getElementById("receive_item_last_cost");
    const itemAverageCost = document.getElementById("receive_item_average_cost");
    if (!barcodeInput || !itemSelect) return;

    const receiveForm = barcodeInput.closest("form");
    const optionList = Array.from(itemSelect.options).filter((opt) => opt.value);

    const normalize = (value) => (value || "").trim().toLowerCase();
    const isTypingField = (el) =>
        el &&
        (
            el.tagName === "INPUT" ||
            el.tagName === "TEXTAREA" ||
            el.tagName === "SELECT" ||
            el.isContentEditable
        );

    function findMatchingOption(rawCode) {
        const code = normalize(rawCode);
        if (!code) return null;

        return (
            optionList.find((opt) => normalize(opt.dataset.barcode) === code) ||
            optionList.find((opt) => normalize(opt.dataset.sku) === code) ||
            optionList.find((opt) => normalize(opt.textContent).includes(code))
        );
    }

    function flashSelectionState(found) {
        itemSelect.classList.remove("receive-scan-found", "receive-scan-missing");
        itemSelect.classList.add(found ? "receive-scan-found" : "receive-scan-missing");
        window.setTimeout(() => {
            itemSelect.classList.remove("receive-scan-found", "receive-scan-missing");
        }, 700);
    }

    function applyScan(code) {
        const match = findMatchingOption(code);
        if (!match) {
            flashSelectionState(false);
            refocusScanner();
            return false;
        }

        itemSelect.value = match.value;
        itemSelect.dispatchEvent(new Event("change", { bubbles: true }));
        flashSelectionState(true);
        refocusScanner();
        return true;
    }

    function formatMoney(amount) {
        const numeric = Number.isFinite(Number(amount)) ? Number(amount) : 0;
        return numeric.toLocaleString("en-US", {
            style: "currency",
            currency: "USD",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function refocusScanner() {
        window.setTimeout(() => {
            if (!barcodeInput.isConnected) return;
            barcodeInput.focus({ preventScroll: true });
            barcodeInput.select();
        }, 60);
    }

    function syncItemFoundPanel(selected) {
        const hasItem = Boolean(selected?.value);
        itemFoundPanel?.classList.toggle("is-found", hasItem);
        itemFoundPanel?.classList.toggle("is-awaiting", !hasItem);

        if (!hasItem) {
            if (itemFoundStatus) itemFoundStatus.textContent = "Awaiting scan";
            if (itemFoundTitle) itemFoundTitle.textContent = "Scan or select an item";
            if (itemFoundName) itemFoundName.textContent = "Use the barcode field or item dropdown to confirm the SKU before posting stock.";
            if (itemCurrentStock) itemCurrentStock.textContent = "--";
            if (itemLastCost) itemLastCost.textContent = formatMoney(0);
            if (itemAverageCost) itemAverageCost.textContent = formatMoney(0);
            return;
        }

        const itemName = selected.dataset.name || selected.textContent?.trim() || "Selected item";
        if (itemFoundStatus) itemFoundStatus.textContent = "Item Found";
        if (itemFoundTitle) itemFoundTitle.textContent = itemName;
        if (itemFoundName) itemFoundName.textContent = selected.dataset.sku ? `SKU ${selected.dataset.sku}` : "SKU not assigned";
        if (itemCurrentStock) itemCurrentStock.textContent = selected.dataset.currentStock || "0";
        if (itemLastCost) itemLastCost.textContent = formatMoney(selected.dataset.lastCost);
        if (itemAverageCost) itemAverageCost.textContent = formatMoney(selected.dataset.averageCost);
    }

    function syncPreview() {
        const selected = itemSelect.options[itemSelect.selectedIndex];
        const quantity = parseFloat(qtyInput?.value || 0) || 0;
        const unitCost = parseFloat(costInput?.value || 0) || 0;
        const locationLabel = locationSelect?.options[locationSelect.selectedIndex]?.textContent?.trim() || "SELECT_NODE";
        const hasItem = Boolean(selected?.value);
        const itemName = hasItem ? (selected.dataset.name || selected.textContent?.trim() || "Selected item") : "Awaiting scan";
        const resolvedCode = hasItem ? (selected.dataset.barcode || selected.dataset.sku || "--") : "--";

        if (liveItem) liveItem.textContent = itemName;
        if (liveCode) liveCode.textContent = resolvedCode || "--";
        if (liveLocation) liveLocation.textContent = locationLabel;
        if (liveMath) liveMath.textContent = `${quantity || 0} x ${unitCost.toFixed(2)}`;
        if (batchTotal) batchTotal.textContent = formatMoney(quantity * unitCost);
        syncItemFoundPanel(hasItem ? selected : null);
    }

    barcodeInput.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        const code = barcodeInput.value.trim();
        if (!code) return;
        applyScan(code);
        barcodeInput.value = "";
        refocusScanner();
    });

    function submitReceiveBatch(event) {
        if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
        if (!receiveForm) return;
        event.preventDefault();
        if (typeof receiveForm.requestSubmit === "function") {
            receiveForm.requestSubmit();
            return;
        }
        receiveForm.submit();
    }

    // Keyboard-wedge scanner support even when focus is not in the scanner field.
    let scanBuffer = "";
    let scanTimer = null;

    function resetBuffer() {
        scanBuffer = "";
        if (scanTimer) {
            clearTimeout(scanTimer);
            scanTimer = null;
        }
    }

    document.addEventListener("keydown", (event) => {
        if (event.ctrlKey || event.altKey || event.metaKey) return;

        const active = document.activeElement;
        const inAnotherInput = isTypingField(active) && active !== barcodeInput;
        if (inAnotherInput) return;

        if (event.key === "Enter") {
            if (scanBuffer.length >= 3) {
                event.preventDefault();
                const consumed = applyScan(scanBuffer);
                if (consumed && active !== barcodeInput) {
                    barcodeInput.value = "";
                    barcodeInput.focus();
                }
            }
            resetBuffer();
            return;
        }

        if (event.key.length === 1) {
            scanBuffer += event.key;
            if (scanTimer) clearTimeout(scanTimer);
            scanTimer = window.setTimeout(resetBuffer, 250);
        }
    });

    itemSelect.addEventListener("change", syncPreview);
    locationSelect?.addEventListener("change", syncPreview);
    qtyInput?.addEventListener("input", syncPreview);
    costInput?.addEventListener("input", syncPreview);
    qtyInput?.addEventListener("keydown", submitReceiveBatch);
    costInput?.addEventListener("keydown", submitReceiveBatch);
    document.addEventListener("stockItemAdded", refocusScanner);
    window.addEventListener("pageshow", refocusScanner);
    syncPreview();
    refocusScanner();
});
