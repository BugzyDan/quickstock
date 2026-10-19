(function() {
    // 1. SELECTORS & STATE
    const state = {
        nameInput: document.getElementById('name_input'),
        liveName: document.getElementById('live_name'),
        locSelect: document.getElementById('location_select'),
        liveLocSidebar: document.getElementById('live_location'),
        liveLocHeader: document.getElementById('header_location'),
        catInput: document.getElementById('category_input'),
        skuPreview: document.getElementById('sku_preview'),
        liveSku: document.getElementById('live_sku'),
        unitCost: document.getElementById('unit_cost'),
        retailPrice: document.getElementById('retail_price'),
        marginEl: document.getElementById('live_margin'),
        currencySelect: document.querySelector('select[name="currency"]'),
        barcodeInput: document.querySelector('[data-barcode-scanner-field]')
    };

    const bindBarcodeScanner = () => {
        const barcodeInput = state.barcodeInput;
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
    };

    // 2. ALPHA MARGIN ENGINE
    // Updated to handle real-time input events and clean formatting
    const calculateMargin = () => {
        if (!state.unitCost || !state.retailPrice || !state.marginEl) return;
        const cost = parseFloat(state.unitCost.value) || 0;
        const retail = parseFloat(state.retailPrice.value) || 0;
        const isUSD = state.currencySelect?.value === 'USD';
        
        if (retail > 0) {
            const margin = ((retail - cost) / retail) * 100;
            state.marginEl.innerText = `${margin.toFixed(1)}%`;
            
            // Visual indicators for your 2026 growth goals
            // High Margin (Success) - targeting your $100k goal
            if (margin >= 40) {
                state.marginEl.style.color = "#00d084"; 
                state.marginEl.style.textShadow = "0 0 8px rgba(0, 208, 132, 0.3)";
            } else if (margin > 15) {
                state.marginEl.style.color = "#2980b9"; 
                state.marginEl.style.textShadow = "none";
            } else {
                state.marginEl.style.color = "#ff4757"; // Warning
            }
        } else {
            state.marginEl.innerText = "0.0%";
            state.marginEl.style.color = "var(--text-muted)";
        }
    };

    // 3. ROBUST SKU ENGINE
    const updateSKU = () => {
        if (!state.catInput || !state.skuPreview || !state.liveSku) return;
        const catValue = state.catInput.value.trim().toUpperCase();
        if (catValue.length >= 2) {
            const catPrefix = catValue.substring(0, 3).padEnd(3, 'X');
            
            if (!state.skuPreview.dataset.suffix) {
                state.skuPreview.dataset.suffix = Math.floor(1000 + Math.random() * 9000);
            }
            
            const now = new Date();
            const dateCode = `${now.getFullYear().toString().slice(-2)}${(now.getMonth() + 1).toString().padStart(2, '0')}`;
            const finalSku = `${catPrefix}-${dateCode}-${state.skuPreview.dataset.suffix}`;
            
            state.skuPreview.value = finalSku;
            state.liveSku.innerText = finalSku;
        }
    };

    // 4. NETWORK NODE SYNC
    const syncLocation = () => {
        if (state.locSelect && state.liveLocSidebar && state.locSelect.selectedIndex > 0) {
            const selectedText = state.locSelect.options[state.locSelect.selectedIndex].text;
            state.liveLocSidebar.innerText = selectedText;
            if (state.liveLocHeader) {
                state.liveLocHeader.innerText = selectedText.toUpperCase().replace(/\s+/g, '_');
                state.liveLocHeader.classList.add('pulse-update');
                setTimeout(() => state.liveLocHeader.classList.remove('pulse-update'), 500);
            }
        }
    };

    // 5. EVENT LISTENERS (The "Glue")
    const initListeners = () => {
        // Financial Triggers
        [state.unitCost, state.retailPrice, state.currencySelect].forEach(el => {
            el?.addEventListener('input', calculateMargin);
        });

        // Nomenclature Triggers
        state.nameInput?.addEventListener('input', (e) => {
            if (state.liveName) {
                state.liveName.innerText = e.target.value.trim().toUpperCase() || "DRAFT_NODE";
            }
        });

        // Category/SKU Triggers
        state.catInput?.addEventListener('input', updateSKU);

        // Location Triggers
        state.locSelect?.addEventListener('change', syncLocation);
        bindBarcodeScanner();
    };

    window.calculateMargin = calculateMargin;
    window.updateSKU = updateSKU;
    window.syncLocation = syncLocation;

    // Initialize Terminal
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            initListeners();
            syncLocation();
            calculateMargin();
            if (!state.skuPreview.value) updateSKU();
        });
    } else {
        initListeners();
        syncLocation();
        calculateMargin();
        if (!state.skuPreview.value) updateSKU();
    }
})();
