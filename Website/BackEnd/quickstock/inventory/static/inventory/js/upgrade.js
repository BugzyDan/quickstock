// WiPay Integration JS
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('checkout-form');
    const billingInput = document.getElementById('billing_cycle');
    const billingPriceMeta = document.getElementById('billing-prices');
    const priceValue = document.getElementById('upgrade-price-value');
    const pricePeriod = document.getElementById('upgrade-price-period');
    const billingOptions = Array.from(document.querySelectorAll('.billing-option'));
    const feedbackBox = document.getElementById('wipay-feedback');

    const yearlyPrice = billingPriceMeta ? billingPriceMeta.dataset.yearly : null;
    const monthlyPrice = billingPriceMeta ? billingPriceMeta.dataset.monthly : null;

    const setFeedback = (message, isError = true) => {
        if (!feedbackBox) {
            if (message) {
                alert(message);
            }
            return;
        }
        feedbackBox.textContent = message || '';
        feedbackBox.classList.toggle('is-hidden', !message);
        feedbackBox.style.background = isError ? 'rgba(239, 68, 68, 0.12)' : 'rgba(34, 197, 94, 0.12)';
        feedbackBox.style.color = isError ? '#b91c1c' : '#166534';
        feedbackBox.style.border = isError ? '1px solid rgba(239, 68, 68, 0.28)' : '1px solid rgba(34, 197, 94, 0.28)';
    };

    const setBillingCycle = (cycle) => {
        if (!billingInput) return;
        const normalized = cycle === 'monthly' ? 'monthly' : 'yearly';
        billingInput.value = normalized;

        billingOptions.forEach((btn) => {
            const isActive = btn.dataset.cycle === normalized;
            btn.classList.toggle('active', isActive);
        });

        if (priceValue && pricePeriod) {
            if (normalized === 'monthly') {
                if (monthlyPrice) {
                    priceValue.innerHTML = `$${monthlyPrice}<span class="price-currency"> JMD</span>`;
                }
                pricePeriod.textContent = 'Per Month';
            } else {
                if (yearlyPrice) {
                    priceValue.innerHTML = `$${yearlyPrice}<span class="price-currency"> JMD</span>`;
                }
                pricePeriod.textContent = 'Per Year';
            }
        }
    };

    if (billingOptions.length) {
        billingOptions.forEach((btn) => {
            btn.addEventListener('click', () => setBillingCycle(btn.dataset.cycle));
        });
        const params = new URLSearchParams(window.location.search);
        const requestedCycle = params.get('billing') || params.get('cycle');
        setBillingCycle(requestedCycle || (billingInput ? billingInput.value : 'yearly'));
    }

    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const btn = form.querySelector('button');
        const defaultLabel = btn ? btn.textContent : 'Upgrade Now';
        if (btn && btn.disabled) {
            setFeedback("WiPay is not configured for this environment.");
            return;
        }

        setFeedback('');
        btn.innerHTML = "Opening Secure Checkout...";
        btn.style.opacity = "0.7";
        btn.disabled = true;

        try {
            const csrfToken = document.querySelector('input[name=csrfmiddlewaretoken]').value;

            const res = await fetch(form.action, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    billing_cycle: billingInput ? billingInput.value : 'yearly'
                })
            });

            const data = await res.json().catch(() => ({}));
            if (data.url) {
                window.location.href = data.url;
            } else {
                const message = data.code === 'provider_unavailable'
                    ? (data.error || 'WiPay is temporarily unavailable. Please try again in a few minutes.')
                    : (data.error || 'WiPay connection failed. Check your API keys.');
                setFeedback(message);
                btn.innerHTML = defaultLabel;
                btn.style.opacity = "1";
                btn.disabled = false;
            }

        } catch (err) {
            setFeedback("Network error. We could not reach the checkout service. Please try again.");
            btn.innerHTML = defaultLabel;
            btn.style.opacity = "1";
            btn.disabled = false;
        }
    });
});
