(function() {
    // --- 1. CONFIGURATION & STATE ---
    // We pull these from the window object defined in the HTML head
    const config = window.QuickStockConfig || {};
    const onMediaQueryChange = (mq, handler) => {
        if (mq && typeof mq.addEventListener === 'function') mq.addEventListener('change', handler);
        else if (mq && typeof mq.addListener === 'function') mq.addListener(handler);
    };
    let cart = [];

    const peep = document.getElementById('scan-peep');
    const successBeep = document.getElementById('success-beep');
    const searchInput = document.getElementById('barcode-input');
    const mobileSearchInput = document.getElementById('cashMobileSearchInput');
    const mobileSearchToggle = document.getElementById('cashMobileSearchToggle');
    const sidebarSearchInput = document.getElementById('cashSidebarSearchInput');
    const mobileSearchClose = document.getElementById('cashMobileSearchClose');
    const mobileSearchOverlay = document.getElementById('cashSearchOverlay');
    const mobileSearchPanel = document.getElementById('cashMobileSearchPanel');
    const mobileSearchQuery = window.matchMedia('(max-width: 900px)');
    let productCards = Array.from(document.querySelectorAll('.product-card'));
    const itemGrid = document.getElementById('item-grid');
    let searchStateNode = null;
    let scanBuffer = "";
    let scanTimer = null;
    let searchFetchTimer = null;
    let activeSearchFetch = null;
    let globalScanBuffer = "";
    let globalScanTimer = null;
    let selectedPaymentChannel = "pos";
    const locationCacheKey = String(config.locationId || "default");
    const offlineItemsKey = `quickstock:pos-items:${locationCacheKey}`;
    const offlineQueueKey = "quickstock:pos-sale-queue";
    const externalOnlineChannels = new Set(["tap2pay", "scan2pay", "wipay2me", "jamdex"]);
    let isSyncingOfflineSales = false;
    const amountPaidInput = document.getElementById('amount-paid');
    const changeDueNode = document.getElementById('change-due');
    const cashTenderPanel = document.getElementById('cash-tender-panel');
    const cashTenderPresetButtons = Array.from(document.querySelectorAll('[data-cash-preset]'));
    const invoiceCustomerPanel = document.getElementById('invoice-customer-panel');
    const invoiceCustomerSelect = document.getElementById('invoice-customer-id');

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
    }

    function formatMoney(value) {
        const amount = parseFloat(value || 0);
        return `$${(Number.isFinite(amount) ? amount : 0).toFixed(2)}`;
    }

    function setPostSaleCard({ tone = "sale", status, number, summaryHtml = "", actionsHtml = "" }) {
        const postSale = document.getElementById('post-sale-actions');
        const statusNode = document.getElementById('post-sale-status');
        const numberNode = document.getElementById('last-sale-num');
        const summaryNode = document.getElementById('post-sale-summary');
        const actionsNode = document.getElementById('post-sale-action-row');
        if (!postSale || !statusNode || !numberNode || !summaryNode || !actionsNode) return;

        postSale.hidden = false;
        postSale.style.display = '';
        postSale.className = `pos-post-sale-card pos-post-sale-card-${tone}`;
        statusNode.textContent = status;
        numberNode.textContent = number;
        summaryNode.innerHTML = summaryHtml;
        actionsNode.innerHTML = actionsHtml;
    }

    function renderReceiptActions(data) {
        const receiptNo = data.receipt_no || data.sale_id;
        const saleId = data.sale_id;
        const receiptUrl = `/receipt/${saleId}/`;
        const printUrl = `/receipt/${saleId}/print/`;
        const downloadUrl = `/receipt/${saleId}/download/`;
        setPostSaleCard({
            tone: "sale",
            status: "Sale complete",
            number: `Receipt #${receiptNo}`,
            summaryHtml: `
                <span><strong>Paid</strong> ${formatMoney(data.amount_paid)}</span>
                <span><strong>Change</strong> ${formatMoney(data.change_due)}</span>
            `,
            actionsHtml: `
                <a id="receipt-link" class="pos-post-sale-action pos-post-sale-action-primary" href="${receiptUrl}" target="_blank" rel="noopener">Open Receipt</a>
                <a class="pos-post-sale-action" href="${printUrl}" target="_blank" rel="noopener">Print</a>
                <a class="pos-post-sale-action" href="${downloadUrl}" target="_blank" rel="noopener">PDF</a>
            `
        });
    }

    function renderInvoiceActions(data) {
        const invoiceNo = data.invoice_no || data.invoice_id;
        const detailUrl = data.detail_url || `/sales/invoices/${data.invoice_id}/`;
        const customerName = data.customer_name ? escapeHtml(data.customer_name) : "Walk-in / Unassigned";
        setPostSaleCard({
            tone: "invoice",
            status: "Invoice created",
            number: invoiceNo,
            summaryHtml: `<span><strong>Customer</strong> ${customerName}</span><span><strong>Status</strong> Issued</span>`,
            actionsHtml: `<a id="receipt-link" class="pos-post-sale-action pos-post-sale-action-primary" href="${detailUrl}" target="_blank" rel="noopener">Open Invoice</a>`
        });
    }

    function renderQueuedSaleActions(payload) {
        const reference = payload.offline_client_ref || makeOfflineClientRef();
        setPostSaleCard({
            tone: "queued",
            status: "Sync pending",
            number: reference,
            summaryHtml: `<span><strong>Saved offline</strong> This transaction will sync when the register reconnects.</span>`,
            actionsHtml: ""
        });
    }

    function setMobileSearchOpen(isOpen) {
        if (!mobileSearchPanel || !mobileSearchOverlay) return;
        const shouldOpen = Boolean(isOpen && mobileSearchQuery.matches);
        document.body.classList.toggle('cash-search-open', shouldOpen);
        mobileSearchOverlay.hidden = !shouldOpen;
        if (mobileSearchToggle) mobileSearchToggle.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        if (mobileSearchInput) mobileSearchInput.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        if (sidebarSearchInput) sidebarSearchInput.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        if (shouldOpen) {
            const nav = document.getElementById('main-nav');
            const navToggle = document.getElementById('navToggle');
            if (nav) nav.classList.remove('mobile-active');
            if (navToggle) {
                navToggle.classList.remove('is-open');
                navToggle.setAttribute('aria-expanded', 'false');
            }
            document.body.classList.remove('qs-mobile-nav-open');
        }
        if (shouldOpen && searchInput && !(mobileSearchInput && document.activeElement === mobileSearchInput) && !(sidebarSearchInput && document.activeElement === sidebarSearchInput)) {
            window.setTimeout(() => {
                searchInput.focus();
            }, 60);
        }
    }

    function getSearchStateNode() {
        if (!itemGrid) return null;
        if (!searchStateNode || !searchStateNode.isConnected || searchStateNode.parentElement !== itemGrid) {
            searchStateNode = document.createElement('div');
            searchStateNode.className = 'cash-search-state';
            searchStateNode.setAttribute('role', 'status');
            itemGrid.prepend(searchStateNode);
        }
        return searchStateNode;
    }

    function setSearchState(message, mode = 'idle') {
        const state = getSearchStateNode();
        if (!state) return;
        state.textContent = message || '';
        state.dataset.mode = mode;
        state.hidden = !message;
    }

    function filterProductCards(query) {
        const q = String(query || '').trim().toLowerCase();
        let matches = 0;
        const searchFirstMobile = mobileSearchQuery.matches;

        productCards.forEach(card => {
            const name = (card.dataset.name || '').toLowerCase();
            const sku = (card.dataset.sku || '').toLowerCase();
            const barcode = (card.dataset.barcode || '').toLowerCase();
            const match = Boolean(q) && (name.includes(q) || sku.includes(q) || barcode.includes(q));
            if (match) matches += 1;
            if (!searchFirstMobile && !q) {
                card.style.display = 'flex';
                return;
            }
            card.style.display = match ? 'flex' : 'none';
        });

        document.body.classList.toggle('cash-has-search-query', Boolean(q));
        document.body.classList.toggle('cash-has-search-results', matches > 0);

        if (searchFirstMobile) {
            if (!q) {
                setSearchState('Scan a barcode or type a product name to begin.', 'idle');
            } else if (matches === 0) {
                setSearchState('No matching products found.', 'empty');
            } else {
                setSearchState(`${matches} matching product${matches === 1 ? '' : 's'}`, 'results');
            }
        } else {
            setSearchState('', 'idle');
        }
    }

    function scheduleServerSearch(query) {
        if (!config.itemsUrl) return;
        const q = String(query || '').trim();
        if (searchFetchTimer) clearTimeout(searchFetchTimer);
        searchFetchTimer = setTimeout(() => {
            reloadItems(q);
        }, q ? 180 : 0);
    }

    function clearProductSearch() {
        if (searchInput) searchInput.value = '';
        if (mobileSearchInput) mobileSearchInput.value = '';
        if (sidebarSearchInput) sidebarSearchInput.value = '';
        filterProductCards('');
    }

    function addVisibleSearchMatch() {
        const visible = productCards.filter(c => c.style.display !== 'none');
        if (visible.length === 1) {
            const c = visible[0];
            let qty = 1;
            const activeMobileValue = sidebarSearchInput && document.activeElement === sidebarSearchInput
                ? sidebarSearchInput.value
                : mobileSearchInput?.value;
            const val = (mobileSearchQuery.matches ? activeMobileValue || '' : searchInput?.value || '').trim();
            const qtyMatch = val.match(/^(\d+)x/i);
            if (qtyMatch) qty = parseInt(qtyMatch[1]);

            window.addToCart(parseInt(c.dataset.id), c.dataset.name, parseFloat(c.dataset.price), qty);
            clearProductSearch();
            setMobileSearchOpen(false);
            return 'added';
        }
        if (visible.length > 1) {
            alert('Multiple items match. Please type more of the name or scan the barcode.');
            return 'multiple';
        }
        return 'none';
    }

    const paymentChannelMeta = {
        pos: {
            label: "POS",
            subtitle: "Point of Sale",
            processing: "FINALIZING POS...",
            button: "FINALIZE POS"
        },
        tap2pay: {
            label: "Tap2Pay",
            subtitle: "Tap terminal checkout",
            processing: "OPENING TAP2PAY...",
            button: "FINALIZE TAP2PAY"
        },
        scan2pay: {
            label: "Scan2Pay",
            subtitle: "QR checkout",
            processing: "OPENING SCAN2PAY...",
            button: "FINALIZE SCAN2PAY"
        },
        wipay2me: {
            label: "WiPay2Me",
            subtitle: "Hosted WiPay link",
            processing: "OPENING WIPAY2ME...",
            button: "FINALIZE WIPAY2ME"
        },
        invoice: {
            label: "Invoice",
            subtitle: "Create issued invoice",
            processing: "CREATING INVOICE...",
            button: "CREATE INVOICE"
        }
    };

    const channelTenderMap = {
        pos: "cash",
        tap2pay: "card",
        scan2pay: "card",
        wipay2me: "card",
        invoice: "invoice"
    };

    function getCurrentTotal() {
        return parseFloat((document.getElementById('total-val')?.innerText || "$0").replace('$', '')) || 0;
    }

    function isCashTenderChannel(channel = selectedPaymentChannel) {
        return (channelTenderMap[channel] || "cash") === "cash";
    }

    function getEnteredAmountPaid() {
        return parseFloat(amountPaidInput ? amountPaidInput.value : 0) || 0;
    }

    function setCheckoutBusy(btn, isBusy, label = "") {
        document.body.classList.toggle('pos-checkout-busy', isBusy);
        if (window.QuickStockLoading) {
            window.QuickStockLoading.setButtonLoading(btn, isBusy, label);
            if (isBusy) window.QuickStockLoading.start();
            else window.QuickStockLoading.finish();
            return;
        }

        if (btn) {
            btn.disabled = isBusy;
            if (label) btn.innerText = label;
        }
    }

    function updateChangeDue(total = null) {
        const resolvedTotal = total === null ? getCurrentTotal() : total;
        const showCashTender = isCashTenderChannel();
        if (cashTenderPanel) {
            cashTenderPanel.style.display = showCashTender ? 'block' : 'none';
        }
        if (!showCashTender) {
            if (changeDueNode) changeDueNode.innerText = '$0.00';
            return;
        }

        const amountPaid = getEnteredAmountPaid();
        const changeDue = Math.max(0, amountPaid - resolvedTotal);
        if (changeDueNode) {
            changeDueNode.innerText = `$${changeDue.toFixed(2)}`;
        }
    }

    function stockBadgeMeta(quantity) {
        const qty = parseInt(quantity || 0, 10) || 0;
        if (qty <= 0) return { label: "Out of stock", tone: "out" };
        if (qty <= 3) return { label: `Low Stock: ${qty.toLocaleString()}`, tone: "low" };
        return { label: `${qty.toLocaleString()} in stock`, tone: "stocked" };
    }

    function renderProductCardHtml(item) {
        const quantity = parseInt(item.stock_quantity ?? item.stock ?? item.quantity ?? 0, 10) || 0;
        const badge = {
            label: item.stock_label || stockBadgeMeta(quantity).label,
            tone: item.stock_tone || stockBadgeMeta(quantity).tone
        };
        return `
            <span class="pos-stock-badge pos-stock-badge-${badge.tone}">${badge.label}</span>
            <span class="card-name">${item.name}</span>
            <span class="card-price">$${parseFloat(item.price).toFixed(2)}</span>`;
    }

    function applyCashTenderPreset(value) {
        if (!amountPaidInput || !isCashTenderChannel()) return;
        const total = getCurrentTotal();
        const preset = String(value || '').toLowerCase();
        const nextAmount = preset === "exact"
            ? total
            : getEnteredAmountPaid() + (parseFloat(preset) || 0);
        amountPaidInput.value = Math.max(0, nextAmount).toFixed(2);
        amountPaidInput.dataset.manual = 'true';
        updateChangeDue(total);
        amountPaidInput.focus();
        amountPaidInput.select();
    }

    const isTypingField = (el) =>
        el &&
        (
            el.tagName === 'INPUT' ||
            el.tagName === 'TEXTAREA' ||
            el.tagName === 'SELECT' ||
            el.isContentEditable
        );

    function readJsonStorage(key, fallback) {
        try {
            const raw = window.localStorage.getItem(key);
            return raw ? JSON.parse(raw) : fallback;
        } catch (e) {
            return fallback;
        }
    }

    function writeJsonStorage(key, value) {
        try {
            window.localStorage.setItem(key, JSON.stringify(value));
            return true;
        } catch (e) {
            console.warn("Unable to write offline POS storage", e);
            return false;
        }
    }

    function getQueuedSales() {
        const queue = readJsonStorage(offlineQueueKey, []);
        return Array.isArray(queue) ? queue : [];
    }

    function saveQueuedSales(queue) {
        writeJsonStorage(offlineQueueKey, Array.isArray(queue) ? queue : []);
        updateOfflineStatus();
    }

    function readCachedItems() {
        const cached = readJsonStorage(offlineItemsKey, null);
        if (!cached || !Array.isArray(cached.items)) return [];
        return cached.items;
    }

    function cacheItems(items) {
        writeJsonStorage(offlineItemsKey, {
            cachedAt: new Date().toISOString(),
            locationId: config.locationId || null,
            items: Array.isArray(items) ? items : []
        });
    }

    function makeProductCardInteractive(card) {
        if (!card) return;
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        if (!card.getAttribute('aria-label')) {
            card.setAttribute('aria-label', `Add ${card.dataset.name || 'item'} to cart`);
        }
    }

    function handleProductCardKeydown(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.currentTarget.click();
    }

    function renderItems(items, fromCache = false) {
        if (!itemGrid) return;
        itemGrid.innerHTML = "";
        if (!items.length) {
            itemGrid.innerHTML = `<div style="padding:16px; color: var(--danger-red); font-weight:800; text-align:center; width:100%;">No inventory is stocked at this location.</div>`;
        } else {
            items.forEach(it => {
                const card = document.createElement('div');
                card.className = 'product-card';
                if (fromCache) card.classList.add('cached-product-card');
                card.dataset.id = it.id;
                card.dataset.name = it.name;
                card.dataset.sku = it.sku || '';
                card.dataset.barcode = it.barcode || '';
                card.dataset.price = it.price;
                card.dataset.stock = it.stock_quantity || 0;
                card.innerHTML = renderProductCardHtml(it);
                makeProductCardInteractive(card);
                card.addEventListener('click', () => {
                    window.addToCart(parseInt(card.dataset.id), card.dataset.name, parseFloat(card.dataset.price));
                });
                card.addEventListener('keydown', handleProductCardKeydown);
                itemGrid.appendChild(card);
            });
        }
        productCards = Array.from(document.querySelectorAll('.product-card'));
        filterProductCards(searchInput ? searchInput.value : '');
        if (fromCache) updateOfflineStatus("cached");
    }

    async function reloadItems(query = '') {
        if (!config.itemsUrl || !itemGrid) return;
        const q = String(query || '').trim();
        try {
            if (activeSearchFetch) activeSearchFetch.abort();
            activeSearchFetch = new AbortController();
            const url = new URL(config.itemsUrl, window.location.origin);
            if (q) url.searchParams.set('q', q);
            const res = await fetch(url.toString(), {
                credentials: "same-origin",
                signal: activeSearchFetch.signal
            });
            if (!res.ok) throw new Error(`Unable to load items (${res.status})`);
            const data = await res.json();
            const items = data.items || [];
            if (!q) cacheItems(items);
            renderItems(items, false);
            updateOfflineStatus();
        } catch (e) {
            if (e && e.name === 'AbortError') return;
            console.warn("Unable to refresh items", e);
            const cachedItems = readCachedItems();
            if (cachedItems.length) {
                renderItems(cachedItems, true);
                filterProductCards(q);
            }
        }
    }

    // --- 2. THEME LOGIC ---
    function applyTheme(mode) { 
        document.documentElement.setAttribute("data-theme-applied", mode); 
    }

    const savedTheme = config.themePref || 'system';
    if (savedTheme === "system") {
        const mq = window.matchMedia("(prefers-color-scheme: dark)");
        applyTheme(mq.matches ? "dark" : "light");
        onMediaQueryChange(mq, e => applyTheme(e.matches ? "dark" : "light"));
    } else {
        applyTheme(savedTheme);
    }

    // --- 3. CORE FUNCTIONS (Globalized for HTML access) ---
    window.addToCart = function(id, name, price, quantity = 1) {
        const postSale = document.getElementById('post-sale-actions');
        if (postSale) postSale.hidden = true;

        if (peep) {
            peep.currentTime = 0;
            peep.play().catch(() => {});
        }

        if (searchInput) {
            clearProductSearch();
            if (mobileSearchQuery.matches && mobileSearchInput) {
                mobileSearchInput.focus();
            } else if (mobileSearchQuery.matches && sidebarSearchInput) {
                sidebarSearchInput.focus();
            } else {
                searchInput.focus();
            }
        }

        const item = cart.find(i => i.id === id);
        if (item) {
            item.quantity += quantity;
        } else {
            cart.push({ id, name, price, quantity });
        }
        updateUI();
    };

    window.updateUI = function() {
        const list = document.getElementById('cart-list');
        if (!list) return;

        list.innerHTML = '';
        let subtotal = 0;

        cart.forEach((item) => {
            subtotal += item.price * item.quantity;
            list.innerHTML += `
                <div class="cart-row">
                    <div class="cart-row-main">
                        <div class="cart-row-name">${item.name}</div>
                        <div class="cart-row-meta">$${item.price.toFixed(2)} each</div>
                        <div class="cart-qty-stepper" aria-label="Quantity controls for ${item.name}">
                            <button type="button" class="cart-qty-btn" onclick="decrementCartItem(${item.id})" aria-label="Decrease ${item.name}">-</button>
                            <input type="number" min="1" value="${item.quantity}" class="cart-qty-input" onchange="setQuantity(${item.id}, this.value)" aria-label="Quantity for ${item.name}">
                            <button type="button" class="cart-qty-btn" onclick="incrementCartItem(${item.id})" aria-label="Increase ${item.name}">+</button>
                            <button type="button" class="cart-remove-btn" onclick="removeFromCart(${item.id})" aria-label="Remove ${item.name}">Remove</button>
                        </div>
                    </div>
                    <div class="cart-row-total">$${(item.price * item.quantity).toFixed(2)}</div>
                </div>`;
        });

        const discInput = document.getElementById('discount-amount');
        const discType = document.getElementById('discount-type');
        
        let disc = parseFloat(discInput ? discInput.value : 0) || 0;
        let type = discType ? discType.value : 'flat';
        let calcDisc = (type === 'percent') ? subtotal * (Math.min(disc, 100) / 100) : disc;

        let total = Math.max(0, subtotal - calcDisc);

        if (amountPaidInput && isCashTenderChannel()) {
            const currentAmount = getEnteredAmountPaid();
            if (!amountPaidInput.dataset.manual || currentAmount <= 0) {
                amountPaidInput.value = total.toFixed(2);
                amountPaidInput.dataset.manual = 'false';
            }
        }
        
        document.getElementById('total-val').innerText = `$${total.toFixed(2)}`;
        document.getElementById('item-count').innerText = cart.reduce((s, i) => s + i.quantity, 0);
        updatePaymentChannelDisplay(total);
        updateChangeDue(total);
    };

    window.setQuantity = function(id, qty) {
        qty = parseInt(qty);
        if (qty <= 0) {
            cart = cart.filter(i => i.id !== id);
        } else {
            const item = cart.find(i => i.id === id);
            if (item) item.quantity = qty;
        }
        updateUI();
    };

    window.incrementCartItem = function(id) {
        const item = cart.find(i => i.id === id);
        if (item) {
            item.quantity += 1;
            updateUI();
        }
    };

    window.decrementCartItem = function(id) {
        const item = cart.find(i => i.id === id);
        if (!item) return;
        item.quantity -= 1;
        if (item.quantity <= 0) {
            cart = cart.filter(i => i.id !== id);
        }
        updateUI();
    };

    window.removeFromCart = function(id) {
        cart = cart.filter(i => i.id !== id);
        updateUI();
    };

    window.voidOrder = function() {
        if(confirm("Void current transaction?")) {
            cart = [];
            const discInput = document.getElementById('discount-amount');
            if (discInput) discInput.value = 0;
            updateUI();
            if (searchInput) searchInput.focus();
        }
    };

    window.toggleMobileMenu = function() {
        const drawer = document.getElementById('mobileDrawer');
        if (drawer) drawer.classList.toggle('drawer-active');
    };

    window.completeCheckout = async function() {
        if (cart.length === 0) return;

        // Use locationId from our config object
        const currentLocationId = config.locationId;

        if (!currentLocationId || currentLocationId === "") {
            alert("CRITICAL ERROR: No branch assigned. Please select a location in the sidebar before finalizing.");
            return;
        }

        const btn = document.getElementById('pay-btn');
        const activeChannel = selectedPaymentChannel || "pos";
        const activeMeta = paymentChannelMeta[activeChannel] || paymentChannelMeta.pos;
        const tender = channelTenderMap[activeChannel] || "cash";

        setCheckoutBusy(btn, true, activeMeta.processing);

        const total = parseFloat(document.getElementById('total-val').innerText.replace('$', ''));
        const amountPaid = getEnteredAmountPaid();
        if (!navigator.onLine && externalOnlineChannels.has(activeChannel)) {
            alert(`${activeMeta.label} needs an internet connection. Use POS/Invoice offline, or reconnect and try again.`);
            setCheckoutBusy(btn, false, activeMeta.button);
            return;
        }
        if (tender === "cash" && amountPaid < total) {
            alert(`Customer payment is short by $${(total - amountPaid).toFixed(2)}.`);
            setCheckoutBusy(btn, false, activeMeta.button);
            return;
        }

        // Pre-checkout integration for external tenders
        if (["tap2pay", "scan2pay", "wipay2me"].includes(activeChannel)) {
            const ok = await runCardCheckout(total, activeChannel);
            if (!ok) {
                setCheckoutBusy(btn, false, activeMeta.button);
                return;
            }
        }
        if (tender === "jamdex") {
            const ok = await runJamdexCheckout(total);
            if (!ok) {
                setCheckoutBusy(btn, false, activeMeta.button);
                return;
            }
        }

        const checkoutPayload = {
            cart: cart,
            total_price: total,
            discount: parseFloat(document.getElementById('discount-amount').value) || 0,
            discount_type: document.getElementById('discount-type').value,
            location_id: Number(currentLocationId),
            tender: tender,
            payment_channel: activeChannel,
            amount_paid: tender === "cash" ? amountPaid : null,
            customer_id: tender === "invoice" && invoiceCustomerSelect ? invoiceCustomerSelect.value : "",
            offline_client_ref: makeOfflineClientRef()
        };

        try {
            const response = await fetch(config.checkoutUrl, {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json", 
                    "X-CSRFToken": config.csrfToken 
                },
                body: JSON.stringify(checkoutPayload)
            });

            const contentType = response.headers.get('content-type') || '';
            let data = null;
            if (contentType.includes('application/json')) {
                data = await response.json();
            } else {
                const text = await response.text();
                throw new Error(text || `Unexpected ${response.status} response`);
            }

            if (data.success) {
                if (successBeep) successBeep.play();
                if (data.type === "invoice") {
                    renderInvoiceActions(data);
                } else {
                    renderReceiptActions(data);
                }
                clearCheckoutForm();
            } else {
                alert(data.error || "Transaction Failed");
            }
        } catch (e) {
            if (canQueueOfflineCheckout(activeChannel)) {
                queueOfflineSale(checkoutPayload);
                alert("Connection dropped, so this POS transaction was saved offline. It will sync automatically when internet returns.");
                showQueuedSaleMessage(checkoutPayload);
                clearCheckoutForm();
            } else {
                alert(e && e.message ? e.message : "Network Error: Check your connection or server status.");
            }
        } finally {
            setCheckoutBusy(btn, false, (paymentChannelMeta[selectedPaymentChannel] || paymentChannelMeta.pos).button);
            if (searchInput) searchInput.focus();
        }
    };

    function clearCheckoutForm() {
        cart = [];
        const discountAmount = document.getElementById('discount-amount');
        if (discountAmount) discountAmount.value = 0;
        if (amountPaidInput) {
            amountPaidInput.value = '0.00';
            amountPaidInput.dataset.manual = 'false';
        }
        updateUI();
    }

    function canQueueOfflineCheckout(channel) {
        return ["pos", "cash", "invoice"].includes(channel);
    }

    function makeOfflineClientRef() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return `WEB-${window.crypto.randomUUID()}`;
        }
        return `WEB-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function queueOfflineSale(payload) {
        const queue = getQueuedSales();
        queue.push({
            id: payload.offline_client_ref || makeOfflineClientRef(),
            status: "pending",
            retries: 0,
            createdAt: new Date().toISOString(),
            lastError: "",
            payload: {
                ...payload,
                cart: payload.cart.map(item => ({ ...item }))
            }
        });
        saveQueuedSales(queue);
    }

    function showQueuedSaleMessage(payload) {
        renderQueuedSaleActions(payload);
    }

    function updatePaymentChannelDisplay(total = null) {
        const meta = paymentChannelMeta[selectedPaymentChannel] || paymentChannelMeta.pos;
        const totalNode = document.getElementById('payment-channel-total');
        const titleNode = document.getElementById('payment-channel-title');
        const subtitleNode = document.getElementById('payment-channel-subtitle');
        const methodNode = document.getElementById('payment-channel-method');
        const payBtn = document.getElementById('pay-btn');

        if (titleNode) titleNode.innerText = selectedPaymentChannel === "invoice" ? "Invoice ready" : "Payment received";
        if (subtitleNode) subtitleNode.innerText = meta.subtitle;
        if (methodNode) methodNode.innerText = meta.label;
        if (payBtn && !payBtn.disabled) payBtn.innerText = meta.button;
        if (invoiceCustomerPanel) {
            invoiceCustomerPanel.style.display = selectedPaymentChannel === "invoice" ? 'block' : 'none';
        }

        if (totalNode) {
            const value = total === null
                ? parseFloat((document.getElementById('total-val')?.innerText || "$0").replace('$', '')) || 0
                : total;
            totalNode.innerText = `$${value.toFixed(2)}`;
        }
        updateChangeDue(total);
    }

    function updateOfflineStatus(mode = "") {
        const statusBox = document.getElementById('offline-status');
        if (!statusBox) return;
        const title = document.getElementById('offline-status-title');
        const copy = document.getElementById('offline-status-copy');
        const pending = getQueuedSales().filter(entry => entry.status !== "synced").length;
        const offline = !navigator.onLine;

        statusBox.hidden = !offline && pending === 0 && mode !== "cached" && mode !== "syncing";
        statusBox.classList.toggle("is-offline", offline);
        statusBox.classList.toggle("has-pending", pending > 0);

        if (mode === "syncing") {
            if (title) title.innerText = "Syncing Offline Sales";
            if (copy) copy.innerText = `${pending} queued transaction${pending === 1 ? "" : "s"} being sent to QuickStock.`;
        } else if (pending > 0) {
            if (title) title.innerText = offline ? "Offline Sales Queued" : "Pending Sales Ready To Sync";
            if (copy) copy.innerText = `${pending} POS transaction${pending === 1 ? "" : "s"} waiting to sync. Keep this register open when online.`;
        } else if (offline || mode === "cached") {
            if (title) title.innerText = "Offline Mode Active";
            if (copy) copy.innerText = "Using cached inventory. POS cash sales and invoices can be queued until internet returns.";
        } else {
            if (title) title.innerText = "Online";
            if (copy) copy.innerText = "Inventory and sales are syncing live.";
        }
    }

    async function syncQueuedSales() {
        if (isSyncingOfflineSales || !navigator.onLine || !config.checkoutUrl) return;
        let queue = getQueuedSales();
        const pending = queue.filter(entry => entry.status !== "synced");
        if (!pending.length) {
            updateOfflineStatus();
            return;
        }

        isSyncingOfflineSales = true;
        updateOfflineStatus("syncing");
        for (const entry of pending) {
            try {
                const response = await fetch(config.checkoutUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": config.csrfToken
                    },
                    credentials: "same-origin",
                    body: JSON.stringify(entry.payload)
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok && data.success) {
                    queue = queue.filter(item => item.id !== entry.id);
                    saveQueuedSales(queue);
                } else {
                    entry.retries = (entry.retries || 0) + 1;
                    entry.lastError = data.error || `Sync failed (${response.status})`;
                    entry.status = "pending";
                    saveQueuedSales(queue);
                    break;
                }
            } catch (e) {
                entry.retries = (entry.retries || 0) + 1;
                entry.lastError = e && e.message ? e.message : "Network error";
                entry.status = "pending";
                saveQueuedSales(queue);
                break;
            }
        }
        isSyncingOfflineSales = false;
        updateOfflineStatus();
    }

    // --- 4. EVENT LISTENERS ---
    if (mobileSearchToggle) {
        mobileSearchToggle.addEventListener('click', () => {
            setMobileSearchOpen(!document.body.classList.contains('cash-search-open'));
        });
    }
    function bindMobileSearchField(input) {
        if (!input) return;
        input.addEventListener('focus', () => {
            setMobileSearchOpen(true);
            filterProductCards(input.value);
        });
        input.addEventListener('input', () => {
            if (searchInput) searchInput.value = input.value;
            if (input !== mobileSearchInput && mobileSearchInput) mobileSearchInput.value = input.value;
            if (input !== sidebarSearchInput && sidebarSearchInput) sidebarSearchInput.value = input.value;
            setMobileSearchOpen(true);
            filterProductCards(input.value);
            scheduleServerSearch(input.value);
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (input.value.trim()) {
                    const result = addVisibleSearchMatch();
                    if (result === 'none') lookupAndAdd(input.value.trim());
                }
            }
        });
    }
    bindMobileSearchField(sidebarSearchInput);
    if (mobileSearchInput) {
        bindMobileSearchField(mobileSearchInput);
    }
    if (mobileSearchClose) {
        mobileSearchClose.addEventListener('click', () => setMobileSearchOpen(false));
    }
    if (mobileSearchOverlay) {
        mobileSearchOverlay.addEventListener('click', () => setMobileSearchOpen(false));
    }
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setMobileSearchOpen(false);
    });
    onMediaQueryChange(mobileSearchQuery, () => {
        if (!mobileSearchQuery.matches) setMobileSearchOpen(false);
        filterProductCards(searchInput ? searchInput.value : '');
    });

    if (searchInput) {
        // Barcode scanners often send fast keypresses ending with Enter
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const code = searchInput.value.trim();
                if (code) {
                    const result = addVisibleSearchMatch();
                    if (result === 'none') lookupAndAdd(code);
                    searchInput.value = '';
                    setMobileSearchOpen(false);
                }
            } else {
                // accumulate chars for auto lookup after short pause
                scanBuffer += e.key.length === 1 ? e.key : '';
                if (scanTimer) clearTimeout(scanTimer);
                scanTimer = setTimeout(() => {
                    if (scanBuffer.length >= 6) {
                        lookupAndAdd(scanBuffer);
                    }
                    scanBuffer = "";
                }, 180);
            }
        });

        searchInput.addEventListener('input', () => {
            if (mobileSearchInput && !mobileSearchQuery.matches) mobileSearchInput.value = searchInput.value;
            filterProductCards(searchInput.value);
            scheduleServerSearch(searchInput.value);
        });
    }

    function bindCards() {
        productCards.forEach(card => {
            const stockQty = parseInt(card.dataset.stock || 0, 10) || 0;
            card.classList.toggle('product-card-out-of-stock', stockQty <= 0);
            makeProductCardInteractive(card);
            card.addEventListener('click', () => {
                window.addToCart(parseInt(card.dataset.id), card.dataset.name, parseFloat(card.dataset.price));
                setMobileSearchOpen(false);
            });
            card.addEventListener('keydown', handleProductCardKeydown);
        });
    }
    bindCards();
    filterProductCards('');

    const discAmt = document.getElementById('discount-amount');
    const discTyp = document.getElementById('discount-type');
    if (discAmt) discAmt.addEventListener('input', window.updateUI);
    if (discTyp) discTyp.addEventListener('change', window.updateUI);
    if (amountPaidInput) {
        amountPaidInput.addEventListener('input', () => {
            amountPaidInput.dataset.manual = 'true';
            cashTenderPresetButtons.forEach(button => {
                button.classList.remove('is-active');
                button.setAttribute('aria-pressed', 'false');
            });
            updateChangeDue();
        });
    }
    cashTenderPresetButtons.forEach(button => {
        button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', () => {
            cashTenderPresetButtons.forEach(option => {
                const active = option === button;
                option.classList.toggle('is-active', active);
                option.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
            applyCashTenderPreset(button.dataset.cashPreset);
        });
    });

    // Payment channel selection
    document.querySelectorAll('[data-payment-channel]').forEach(btn => {
        btn.addEventListener('click', () => {
            selectedPaymentChannel = btn.dataset.paymentChannel || "pos";
            document.querySelectorAll('[data-payment-channel]').forEach(option => {
                const active = option === btn;
                option.classList.toggle('active', active);
                option.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
            if (amountPaidInput && isCashTenderChannel() && getEnteredAmountPaid() <= 0) {
                amountPaidInput.value = getCurrentTotal().toFixed(2);
                amountPaidInput.dataset.manual = 'false';
            }
            updatePaymentChannelDisplay();
        });
        if (btn.classList.contains('active')) {
            selectedPaymentChannel = btn.dataset.paymentChannel || "pos";
        }
    });
    updatePaymentChannelDisplay();

    document.addEventListener('keydown', (e) => {
        const active = document.activeElement;
        const typing = isTypingField(active);
        if (e.key === 'F2' || (e.key === '/' && !typing)) {
            e.preventDefault();
            setMobileSearchOpen(true);
            if (searchInput) {
                searchInput.focus();
                searchInput.select();
            }
            return;
        }
        if (e.key === 'Enter' && cart.length > 0 && !typing) {
            e.preventDefault();
            window.completeCheckout();
            return;
        }
        if (e.key === 'Escape') {
            if (document.body.classList.contains('cash-search-open')) {
                setMobileSearchOpen(false);
                return;
            }
            if (cart.length > 0) {
                e.preventDefault();
                window.voidOrder();
            }
        }
    });

    async function runCardCheckout(total, paymentChannel = "card") {
        if (!config.cardCheckoutUrl) return true;
        try {
            const reference = `POS-${paymentChannel.toUpperCase()}-${Date.now()}`;
            const res = await fetch(config.cardCheckoutUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": config.csrfToken
                },
                credentials: "same-origin",
                body: JSON.stringify({ total, reference, payment_channel: paymentChannel })
            });
            const data = await res.json().catch(() => ({}));
            if (!data.success) {
                const message = data.code === "provider_unavailable"
                    ? (data.error || "WiPay is temporarily unavailable. Please try again in a few minutes.")
                    : (data.error || "Card checkout could not be started.");
                alert(message);
                return false;
            }
            if (data.checkout_url) {
                window.open(data.checkout_url, "_blank", "noopener,noreferrer");
            }
            const label = (paymentChannelMeta[paymentChannel] || paymentChannelMeta.tap2pay).label;
            return window.confirm(`Complete the ${label} payment, then click OK to finalize this sale.`);
        } catch (e) {
            alert("Payment checkout failed. Please try again in a few minutes.");
            return false;
        }
    }

    async function runJamdexCheckout(total) {
        if (!config.jamdexCheckoutUrl) return true;
        try {
            const reference = `POS-JD-${Date.now()}`;
            const res = await fetch(config.jamdexCheckoutUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": config.csrfToken
                },
                credentials: "same-origin",
                body: JSON.stringify({ total, reference })
            });
            const data = await res.json();
            if (!data.success) {
                alert(data.error || "JAM-DEX checkout could not be started.");
                return false;
            }
            return window.confirm("Complete the JAM-DEX payment, then click OK to finalize this sale.");
        } catch (e) {
            alert("JAM-DEX checkout failed. Please try again.");
            return false;
        }
    }

    // --- 5. BACKGROUND SERVICES ---
    setInterval(() => {
        fetch(config.roleStatusUrl, { credentials: "same-origin" })
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (!data || !data.role) return;
                if (data.role !== config.currentRole && data.redirect) {
                    window.location.href = data.redirect;
                }
            })
            .catch(() => {});
    }, 10000);

    // Refresh items periodically to reflect admin moves
    setInterval(reloadItems, 15000);
    reloadItems();

    async function lookupAndAdd(code) {
        if (!config.lookupUrl) return;
        try {
            const res = await fetch(`${config.lookupUrl}?q=${encodeURIComponent(code)}`, { credentials: "same-origin" });
            if (!res.ok) throw new Error("Lookup unavailable");
            const data = await res.json();
            if (data && data.success && data.item) {
                window.addToCart(parseInt(data.item.id), data.item.name, parseFloat(data.item.price));
            }
        } catch (e) {
            console.warn("Lookup failed", e);
            const lowered = String(code || "").trim().toLowerCase();
            const cachedItem = readCachedItems().find(item =>
                String(item.barcode || "").toLowerCase() === lowered ||
                String(item.sku || "").toLowerCase() === lowered
            );
            if (cachedItem) {
                window.addToCart(parseInt(cachedItem.id), cachedItem.name, parseFloat(cachedItem.price));
            }
        }
    }

    // Support keyboard-wedge scanners even when search input is not focused.
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.altKey || e.metaKey) return;

        const active = document.activeElement;
        const typingElsewhere = isTypingField(active) && active !== searchInput;
        if (typingElsewhere) return;

        if (e.key === 'Enter') {
            if (globalScanBuffer.length >= 6) {
                e.preventDefault();
                lookupAndAdd(globalScanBuffer);
                if (searchInput) {
                    searchInput.value = '';
                    searchInput.focus();
                }
            }
            globalScanBuffer = '';
            if (globalScanTimer) {
                clearTimeout(globalScanTimer);
                globalScanTimer = null;
            }
            return;
        }

        if (e.key.length === 1) {
            globalScanBuffer += e.key;
            if (globalScanTimer) clearTimeout(globalScanTimer);
            globalScanTimer = setTimeout(() => {
                globalScanBuffer = '';
            }, 250);
        }
    });

    window.addEventListener('online', () => {
        updateOfflineStatus();
        reloadItems();
        syncQueuedSales();
    });
    window.addEventListener('offline', updateOfflineStatus);
    setInterval(syncQueuedSales, 30000);
    updateOfflineStatus();
    syncQueuedSales();

})();
