/**
 * QuickStock Inventory Terminal Logic
 * Handles real-time SKU generation, margin calculation, and UI sync.
 */
(function() {
    const config = window.TerminalConfig || {};
    
    // UI Elements
    const elements = {
        unitCost: document.getElementById('unit_cost'),
        sellingPrice: document.getElementById('price'),
        marginDisplay: document.getElementById('live_margin'),
        categoryInput: document.getElementById('category'),
        skuInput: document.getElementById('sku_preview'),
        liveSku: document.getElementById('live_sku'),
        productName: document.getElementById('product_name'),
        liveName: document.getElementById('live_name'),
        locationSelect: document.getElementById('location'),
        liveLocation: document.getElementById('live_location'),
        headerNode: document.getElementById('header_node'),
        supplierSelect: document.getElementById('supplier'),
        liveSupplier: document.getElementById('live_supplier'),
        barcodeInput: document.querySelector('[data-barcode-scanner-field]')
    };

    function bindBarcodeScanner() {
        const barcodeInput = elements.barcodeInput;
        if (!barcodeInput || barcodeInput.dataset.scannerBound === 'true') return;

        barcodeInput.dataset.scannerBound = 'true';

        const commitBarcode = (rawCode) => {
            const code = String(rawCode || '').replace(/[\r\n\t]+/g, '').trim();
            if (!code) return false;
            barcodeInput.value = code;
            barcodeInput.dispatchEvent(new Event('input', { bubbles: true }));
            barcodeInput.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        };

        barcodeInput.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            if (commitBarcode(barcodeInput.value)) {
                event.preventDefault();
            }
        });

        let scanBuffer = '';
        let lastKeyTime = 0;
        const scannerGapMs = 45;

        document.addEventListener('keydown', (event) => {
            if (event.ctrlKey || event.altKey || event.metaKey) return;

            const active = document.activeElement;
            const activeIsEditable = active && (
                active.tagName === 'INPUT' ||
                active.tagName === 'TEXTAREA' ||
                active.tagName === 'SELECT' ||
                active.isContentEditable
            );

            if (activeIsEditable && active !== barcodeInput) return;

            if (event.key === 'Enter') {
                if (scanBuffer.length >= 6 && commitBarcode(scanBuffer)) {
                    event.preventDefault();
                    barcodeInput.focus();
                }
                scanBuffer = '';
                return;
            }

            if (event.key.length !== 1) return;

            const now = Date.now();
            scanBuffer = now - lastKeyTime <= scannerGapMs ? scanBuffer + event.key : event.key;
            lastKeyTime = now;
        }, true);
    }

    // --- FINANCIAL LOGIC ---
    window.calculateMargin = function() {
        if (!elements.unitCost || !elements.sellingPrice || !elements.marginDisplay) return;
        const cost = parseFloat(elements.unitCost.value) || 0;
        const msrp = parseFloat(elements.sellingPrice.value) || 0;
        
        if (msrp > 0) {
            const margin = ((msrp - cost) / msrp) * 100;
            elements.marginDisplay.innerText = `${margin.toFixed(1)}% MARGIN`;
            
            // Visual feedback based on profitability
            if (margin > 20) {
                elements.marginDisplay.style.background = "rgba(39, 174, 96, 0.1)";
                elements.marginDisplay.style.color = "#27ae60"; // Success Green
            } else if (margin > 0) {
                elements.marginDisplay.style.background = "rgba(241, 196, 15, 0.1)";
                elements.marginDisplay.style.color = "#f39c12"; // Warning Gold
            } else {
                elements.marginDisplay.style.background = "rgba(235, 77, 75, 0.1)";
                elements.marginDisplay.style.color = "#eb4d4b"; // Danger Red
            }
        } else {
            elements.marginDisplay.innerText = "0.0% MARGIN";
            elements.marginDisplay.style.background = "#f1f2f6";
            elements.marginDisplay.style.color = "#2d3436";
        }
    };

    // --- SKU GENERATION LOGIC ---
    window.updateSKU = function() {
        if (!elements.categoryInput || !elements.skuInput || !elements.liveSku) return;
        // If we are updating an existing item, do not regenerate the SKU automatically
        if (config.isUpdate && config.existingSku) {
            elements.skuInput.value = config.existingSku;
            elements.liveSku.innerText = config.existingSku;
            return;
        }

        let catValue = elements.categoryInput.value.trim().toUpperCase();
        let prefix = catValue.length >= 3 ? catValue.substring(0, 3) : "GEN";

        const now = new Date();
        const dateStr = `${String(now.getFullYear()).slice(-2)}${String(now.getMonth() + 1).padStart(2, '0')}`;

        if (!elements.skuInput.dataset.suffix) {
            elements.skuInput.dataset.suffix = Math.floor(100 + Math.random() * 900);
        }

        const finalSku = `${prefix}-${dateStr}-${elements.skuInput.dataset.suffix}`;
        elements.skuInput.value = finalSku;
        elements.liveSku.innerText = finalSku;
    };

    // --- UI SYNC HELPERS ---
    window.updateLocationPreview = function() {
        if (!elements.locationSelect || !elements.liveLocation) return;
        const selectedText = elements.locationSelect.options[elements.locationSelect.selectedIndex]?.text;
        const val = selectedText || "SELECT_NODE";
        elements.liveLocation.innerText = val;
        if (elements.headerNode) {
            elements.headerNode.innerText = val.toUpperCase();
        }
    };

    window.updateSupplierPreview = function() {
        if (!elements.supplierSelect || !elements.liveSupplier) return;
        const selectedText = elements.supplierSelect.options[elements.supplierSelect.selectedIndex]?.text;
        elements.liveSupplier.innerText = selectedText || "DIRECT_ENTRY";
    };

    // --- INITIALIZATION ---
    document.addEventListener('DOMContentLoaded', () => {
        // Run initial sync
        updateSKU();
        calculateMargin();
        updateLocationPreview();
        updateSupplierPreview();
        bindBarcodeScanner();

        // Attach listeners for live updates
        elements.productName?.addEventListener('input', (e) => {
            if (elements.liveName) {
                elements.liveName.innerText = e.target.value || 'DRAFT_ITEM';
            }
        });

        elements.unitCost?.addEventListener('input', calculateMargin);
        elements.sellingPrice?.addEventListener('input', calculateMargin);
        elements.categoryInput?.addEventListener('input', updateSKU);
        elements.locationSelect?.addEventListener('change', updateLocationPreview);
        elements.supplierSelect?.addEventListener('change', updateSupplierPreview);
    });
})();
