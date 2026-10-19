
    const amountInput = document.getElementById('id_amount');
    const gctDisplay = document.getElementById('gct_val');
    const baseDisplay = document.getElementById('base_val');

    amountInput.addEventListener('input', function() {
        const total = parseFloat(this.value) || 0;
        const base = total / 1.15;
        const gct = total - base;

        gctDisplay.innerText = '$' + gct.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
        baseDisplay.innerText = '$' + base.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    });

