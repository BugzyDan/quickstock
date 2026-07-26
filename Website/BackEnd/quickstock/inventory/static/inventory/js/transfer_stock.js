document.addEventListener('DOMContentLoaded', function () {
    const stockNode = document.getElementById('quickstockTransferStockData');
    const stockData = stockNode ? JSON.parse(stockNode.textContent || '{}') : {};
    const itemSearch = document.getElementById('item-search');
    const searchResults = document.getElementById('transfer-search-results');
    const itemSelect = document.getElementById('item-select');
    const fromSelect = document.getElementById('from-location');
    const toSelect = document.getElementById('to-location');
    const qtyInput = document.getElementById('qty-input');
    const stockList = document.getElementById('stock-list');
    const submitBtn = document.getElementById('submit-btn');
    const locationError = document.getElementById('location-error');
    const qtyError = document.getElementById('qty-error');
    const selectedItemBadge = document.getElementById('selected-item-badge');
    const sourcePreview = document.getElementById('transfer-source-preview');
    const destinationPreview = document.getElementById('transfer-destination-preview');
    const qtyPreview = document.getElementById('transfer-qty-preview');
    const liveTitle = document.getElementById('transfer-live-title');
    const sourceRoute = document.getElementById('transfer-source-route');
    const destinationRoute = document.getElementById('transfer-destination-route');
    const sourceFlow = document.getElementById('transfer-source-flow');
    const destinationFlow = document.getElementById('transfer-destination-flow');
    const liveNote = document.getElementById('transfer-live-note');

    if (!itemSelect || !fromSelect || !toSelect || !qtyInput || !stockList || !submitBtn) return;

    const itemOptions = Array.from(itemSelect.options).filter(option => option.value);
    let filteredOptions = itemOptions.slice();
    let activeResultIndex = -1;

    function normalize(value) {
        return (value || '').toString().trim().toLowerCase();
    }

    function formatOptionLabel(option) {
        return (option.textContent || '').trim();
    }

    function formatNumber(value) {
        return Number(value || 0).toLocaleString();
    }

    function getItemData() {
        const itemId = itemSelect.value;
        const itemData = stockData[itemId];
        if (!itemData) return null;

        if (Array.isArray(itemData)) {
            return {
                name: itemSelect.options[itemSelect.selectedIndex]?.textContent?.trim() || 'Selected item',
                locations: itemData,
            };
        }

        return itemData;
    }

    function getLocationRecord(locationId) {
        const itemData = getItemData();
        if (!itemData || !locationId) return null;
        return (itemData.locations || []).find(record => String(record.id) === String(locationId)) || null;
    }

    function getLocationName(selectElement) {
        return (selectElement.options[selectElement.selectedIndex]?.textContent || '')
            .replace(/\s+\([^)]+\)\s*$/, '')
            .trim();
    }

    function closeSearchResults() {
        if (!searchResults || !itemSearch) return;
        searchResults.innerHTML = '';
        searchResults.classList.remove('is-open');
        itemSearch.setAttribute('aria-expanded', 'false');
        activeResultIndex = -1;
    }

    function chooseItem(option, syncSearch = true) {
        if (!option) return;
        itemSelect.value = option.value;
        if (itemSearch && syncSearch) {
            itemSearch.value = formatOptionLabel(option);
        }
        closeSearchResults();
        updateUI();
    }

    function renderSearchResults(query) {
        if (!searchResults || !itemSearch) return;

        searchResults.innerHTML = '';
        activeResultIndex = -1;

        if (!query || !filteredOptions.length) {
            searchResults.classList.remove('is-open');
            itemSearch.setAttribute('aria-expanded', 'false');
            return;
        }

        filteredOptions.slice(0, 8).forEach((option, index) => {
            const result = document.createElement('button');
            result.type = 'button';
            result.className = 'transfer-search-result';
            result.setAttribute('role', 'option');
            result.dataset.index = String(index);
            result.textContent = formatOptionLabel(option);
            result.addEventListener('mousedown', function (event) {
                event.preventDefault();
                chooseItem(option);
            });
            searchResults.appendChild(result);
        });

        searchResults.classList.add('is-open');
        itemSearch.setAttribute('aria-expanded', 'true');
    }

    function highlightSearchResult(nextIndex) {
        if (!searchResults) return;
        const results = Array.from(searchResults.querySelectorAll('.transfer-search-result'));
        if (!results.length) {
            activeResultIndex = -1;
            return;
        }

        activeResultIndex = Math.max(0, Math.min(nextIndex, results.length - 1));
        results.forEach((result, index) => {
            result.classList.toggle('is-active', index === activeResultIndex);
        });
    }

    function filterItems() {
        if (!itemSearch) return;

        const query = normalize(itemSearch.value);
        let onlyVisibleOption = null;

        filteredOptions = itemOptions.filter(option => {
            const haystack = normalize(option.dataset.search || option.textContent);
            return !query || haystack.includes(query);
        });

        itemOptions.forEach(option => {
            const isVisible = filteredOptions.includes(option);
            option.hidden = !isVisible;
            option.disabled = !isVisible;
            if (isVisible && filteredOptions.length === 1) {
                onlyVisibleOption = option;
            }
        });

        if (itemSelect.value) {
            const selected = itemSelect.options[itemSelect.selectedIndex];
            if (selected && selected.hidden) {
                itemSelect.value = '';
                updateUI();
            }
        }

        if (filteredOptions.length === 1 && query) {
            chooseItem(onlyVisibleOption, false);
        } else {
            renderSearchResults(query);
        }
    }

    function validateForm() {
        const selectedFromOpt = fromSelect.options[fromSelect.selectedIndex];
        const currentMax = selectedFromOpt ? parseInt(selectedFromOpt.dataset.max || 0, 10) : 0;
        const enteredQty = parseInt(qtyInput.value || 0, 10);
        const sourceName = getLocationName(fromSelect);
        const destName = getLocationName(toSelect);
        const sourceValue = (fromSelect.value || '').trim().toLowerCase();
        const destValue = (toSelect.value || '').trim().toLowerCase();
        const safeQty = enteredQty > 0 ? enteredQty : 0;
        const destinationRecord = getLocationRecord(toSelect.value);
        const destinationQty = destinationRecord ? parseInt(destinationRecord.qty || 0, 10) : 0;
        const hasItem = !!itemSelect.value;

        if (sourcePreview) sourcePreview.textContent = sourceName || 'Awaiting source';
        if (destinationPreview) destinationPreview.textContent = destName || 'Awaiting destination';
        if (qtyPreview) qtyPreview.textContent = `${safeQty} unit${safeQty === 1 ? '' : 's'}`;

        if (liveTitle) {
            liveTitle.textContent = hasItem
                ? `Moving ${formatNumber(safeQty)} unit${safeQty === 1 ? '' : 's'}`
                : 'Select an item to preview movement';
        }
        if (sourceRoute) sourceRoute.textContent = sourceName || 'Awaiting source';
        if (destinationRoute) destinationRoute.textContent = destName || 'Awaiting destination';
        if (sourceFlow) sourceFlow.textContent = `${formatNumber(currentMax)} → ${formatNumber(Math.max(currentMax - safeQty, 0))}`;
        if (destinationFlow) destinationFlow.textContent = `${formatNumber(destinationQty)} → ${formatNumber(destinationQty + safeQty)}`;

        let hasError = false;
        const hasLocationConflict = !!sourceValue && !!destValue && sourceValue === destValue;
        const hasQtyConflict = enteredQty > currentMax || enteredQty <= 0;

        if (hasQtyConflict || hasLocationConflict) {
            hasError = true;
        }

        if (locationError) locationError.style.display = hasLocationConflict ? 'block' : 'none';
        if (qtyError) qtyError.style.display = hasQtyConflict && !!qtyInput.value ? 'block' : 'none';
        fromSelect.classList.toggle('input-error', hasLocationConflict);
        toSelect.classList.toggle('input-error', hasLocationConflict);
        qtyInput.classList.toggle('input-error', hasQtyConflict && !!qtyInput.value);

        if (liveNote) {
            if (!hasItem) {
                liveNote.textContent = 'Choose an item to load current balances by branch.';
            } else if (!sourceValue || !toSelect.value) {
                liveNote.textContent = 'Choose both locations to verify the route.';
            } else if (hasLocationConflict) {
                liveNote.textContent = 'Source and destination must be different locations.';
            } else if (enteredQty > currentMax) {
                liveNote.textContent = `Only ${formatNumber(currentMax)} unit${currentMax === 1 ? '' : 's'} available at ${sourceName}.`;
            } else if (enteredQty <= 0) {
                liveNote.textContent = 'Enter a positive quantity to preview the transfer.';
            } else {
                liveNote.textContent = `${formatNumber(safeQty)} unit${safeQty === 1 ? '' : 's'} will move from ${sourceName} to ${destName}.`;
            }
        }

        submitBtn.disabled = hasError || !hasItem || !sourceValue || !toSelect.value;
    }

    function updateUI() {
        const itemData = getItemData();
        stockList.innerHTML = '';
        fromSelect.innerHTML = '<option value="">Select source...</option>';
        if (selectedItemBadge) {
            const selectedLabel = itemData?.name || itemSelect.options[itemSelect.selectedIndex]?.textContent || '';
            selectedItemBadge.textContent = selectedLabel ? selectedLabel.trim() : 'Awaiting selection';
        }

        if (itemData && itemData.locations && itemData.locations.length) {
            itemData.locations.forEach(record => {
                const recordQty = parseInt(record.qty || 0, 10);
                const div = document.createElement('div');
                div.className = 'preview-item';
                div.innerHTML = `<span>${record.name}</span><span class="preview-qty">${formatNumber(recordQty)}</span>`;
                stockList.appendChild(div);

                if (recordQty <= 0) return;
                const opt = document.createElement('option');
                opt.value = record.id;
                opt.dataset.max = recordQty;
                opt.textContent = `${record.name} (${formatNumber(recordQty)} available)`;
                fromSelect.appendChild(opt);
            });
        } else {
            stockList.innerHTML = '<p class="transfer-empty-copy">No source balances available for this item yet.</p>';
        }
        validateForm();
    }

    itemSelect.addEventListener('change', updateUI);
    if (itemSearch) {
        itemSearch.addEventListener('input', filterItems);
        itemSearch.addEventListener('keydown', function (event) {
            const results = searchResults ? Array.from(searchResults.querySelectorAll('.transfer-search-result')) : [];

            if (event.key === 'ArrowDown' && results.length) {
                event.preventDefault();
                highlightSearchResult(activeResultIndex + 1);
                return;
            }

            if (event.key === 'ArrowUp' && results.length) {
                event.preventDefault();
                highlightSearchResult(activeResultIndex - 1);
                return;
            }

            if (event.key === 'Escape') {
                closeSearchResults();
                return;
            }

            if (event.key === 'Enter') {
                event.preventDefault();
                if (activeResultIndex >= 0 && filteredOptions[activeResultIndex]) {
                    chooseItem(filteredOptions[activeResultIndex]);
                    return;
                }
                if (filteredOptions.length === 1) {
                    chooseItem(filteredOptions[0]);
                    return;
                }
                filterItems();
                itemSelect.focus();
            }
        });
        itemSearch.addEventListener('focus', filterItems);
        itemSearch.addEventListener('blur', function () {
            window.setTimeout(closeSearchResults, 120);
        });
    }
    document.addEventListener('click', function (event) {
        if (!searchResults || !itemSearch) return;
        if (event.target === itemSearch || searchResults.contains(event.target)) return;
        closeSearchResults();
    });
    fromSelect.addEventListener('change', validateForm);
    toSelect.addEventListener('change', validateForm);
    qtyInput.addEventListener('input', validateForm);
});
