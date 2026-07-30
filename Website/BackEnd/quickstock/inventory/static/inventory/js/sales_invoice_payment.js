(function () {
    const configNode = document.getElementById('salesInvoicePaymentConfig');
    const amountInput = document.getElementById('payment-amount-input');
    const creditInput = document.getElementById('credit-to-apply-input');
    const creditAppliedDisplay = document.getElementById('credit-applied-display');
    const remainingAfterCreditDisplay = document.getElementById('remaining-after-credit-display');
    const amountOverDisplay = document.getElementById('amount-over-display');
    const availableCreditDisplay = document.getElementById('available-credit-display');
    const overagePanel = document.getElementById('overage-highlight-panel');
    const overageCopy = document.getElementById('overage-highlight-copy');
    const payFullBalanceButton = document.getElementById('pay-full-balance-button');
    const paymentMethodSelect = document.getElementById('payment-method-select');
    const referenceInput = document.getElementById('payment-reference-input');
    const referenceRequiredCopy = document.getElementById('payment-reference-required-copy');
    const collectedNowRadio = document.getElementById('collected-now-radio');
    const leaveInStoreRadio = document.getElementById('leave-in-store-radio');
    const pickupLockNote = document.getElementById('pickup-lock-note');

    if (!configNode || !amountInput) return;

    let config = {};
    try {
        config = JSON.parse(configNode.textContent || '{}');
    } catch (error) {
        config = {};
    }

    const balanceDue = parseFloat(config.balanceDue || '0') || 0;
    const availableCustomerCredit = parseFloat(config.availableCustomerCredit || '0') || 0;

    function readMoney(input) {
        if (!input) return 0;
        return Math.max(0, parseFloat(input.value || '0') || 0);
    }

    function formatMoney(value) {
        return `$${value.toFixed(2)}`;
    }

    function settlementTotal() {
        return readMoney(amountInput) + Math.min(readMoney(creditInput), availableCustomerCredit, balanceDue);
    }

    function updatePickupState() {
        if (!collectedNowRadio || !leaveInStoreRadio) return;
        const isFullySettled = settlementTotal() + 0.001 >= balanceDue;
        collectedNowRadio.disabled = !isFullySettled;
        const pickupOption = collectedNowRadio.closest('.sales-pickup-option');
        if (pickupOption) {
            pickupOption.classList.toggle('is-disabled', !isFullySettled);
        }
        if (!isFullySettled && collectedNowRadio.checked) {
            leaveInStoreRadio.checked = true;
        }
        if (pickupLockNote) {
            pickupLockNote.hidden = isFullySettled;
        }
    }

    function updateReferenceRequirement() {
        if (!paymentMethodSelect || !referenceInput) return;
        const method = (paymentMethodSelect.value || '').toLowerCase();
        const requiresReference = method !== '' && method !== 'cash' && method !== 'account_credit';
        referenceInput.required = requiresReference;
        referenceInput.setAttribute('aria-required', requiresReference ? 'true' : 'false');
        if (referenceRequiredCopy) {
            referenceRequiredCopy.hidden = !requiresReference;
        }
    }

    function updateCreditPreview() {
        const enteredCredit = Math.min(readMoney(creditInput), availableCustomerCredit, balanceDue);
        const remainingAfterCredit = Math.max(0, balanceDue - enteredCredit);
        const currentAmount = readMoney(amountInput);
        if (enteredCredit > 0 && currentAmount === balanceDue) {
            amountInput.value = '0.00';
        } else if (enteredCredit > 0 && currentAmount > remainingAfterCredit) {
            amountInput.value = remainingAfterCredit.toFixed(2);
        }
        const amountEntered = readMoney(amountInput);
        const amountOver = Math.max(0, amountEntered - remainingAfterCredit);

        if (creditInput && enteredCredit !== readMoney(creditInput)) {
            creditInput.value = enteredCredit.toFixed(2);
        }

        if (availableCreditDisplay) availableCreditDisplay.textContent = formatMoney(availableCustomerCredit);
        if (creditAppliedDisplay) creditAppliedDisplay.textContent = formatMoney(enteredCredit);
        if (remainingAfterCreditDisplay) remainingAfterCreditDisplay.textContent = formatMoney(remainingAfterCredit);
        if (amountOverDisplay) amountOverDisplay.textContent = formatMoney(amountOver);

        if (amountOverDisplay) {
            amountOverDisplay.style.color = amountOver > 0 ? '#2563eb' : '';
        }

        if (overagePanel && overageCopy) {
            overagePanel.hidden = amountOver <= 0;
            if (amountOver > 0) {
                const creditMessage = availableCustomerCredit > 0 || creditInput
                    ? `Over by ${formatMoney(amountOver)}. This extra amount can be stored on the customer's account as credit.`
                    : `Over by ${formatMoney(amountOver)}. Assign a customer to store this extra amount as credit.`;
                overageCopy.textContent = creditMessage;
            }
        }

        updatePickupState();
    }

    amountInput.addEventListener('input', updateCreditPreview);
    if (creditInput) {
        creditInput.addEventListener('input', updateCreditPreview);
    }
    if (payFullBalanceButton) {
        payFullBalanceButton.addEventListener('click', () => {
            const creditApplied = Math.min(readMoney(creditInput), availableCustomerCredit, balanceDue);
            const amountNeeded = Math.max(0, balanceDue - creditApplied);
            amountInput.value = amountNeeded.toFixed(2);
            amountInput.focus();
            updateCreditPreview();
        });
    }
    if (paymentMethodSelect) {
        paymentMethodSelect.addEventListener('change', updateReferenceRequirement);
    }

    updateReferenceRequirement();
    updateCreditPreview();
})();
