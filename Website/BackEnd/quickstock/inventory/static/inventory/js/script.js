// DOM elements
const barcodeInput = document.getElementById('barcode-input');
const productSelect = document.getElementById('product-select');
const addButton = document.querySelector('.btn-add');
const cartBody = document.getElementById('cart-body');
const cartTotal = document.getElementById('cart-total');
const completeButton = document.querySelector('.btn-complete');

// New SideNav Elements
const sideNav = document.getElementById('sideNav');
const sidebarOverlay = document.getElementById('sidebarOverlay');

let cart = [];

// ---------- Navigation Functions ----------

/**
 * Toggles the mobile sidebar and background overlay.
 * Also locks body scroll to prevent "double scrolling."
 */
function toggleSidebar() {
    if (!sideNav || !sidebarOverlay) return; // Guard clause
    
    sideNav.classList.toggle('active');
    sidebarOverlay.classList.toggle('active');

    // Smooth body scroll lock
    if (sideNav.classList.contains('active')) {
        document.body.style.overflow = 'hidden';
    } else {
        document.body.style.overflow = 'auto';
    }
}

// Close sidebar automatically if screen is resized to desktop width
window.addEventListener('resize', () => {
    if (window.innerWidth > 768 && sideNav && sideNav.classList.contains('active')) {
        toggleSidebar();
    }
});

function initIndexMobileNav() {
    if (!document.body.classList.contains('page-index')) return;
    const navToggle = document.getElementById('navToggle');
    const nav = document.getElementById('main-nav');
    if (!navToggle || !nav) return;

    if (navToggle.dataset.bound === 'true') return;
    navToggle.dataset.bound = 'true';
    let lastToggleAt = 0;

    const toggleNav = (e) => {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }
        const now = Date.now();
        if (now - lastToggleAt < 250) return;
        lastToggleAt = now;
        nav.classList.toggle('mobile-active');
        navToggle.classList.toggle('is-open');
    };

    navToggle.addEventListener('click', toggleNav);
    navToggle.addEventListener('pointerup', toggleNav);

    document.addEventListener('click', (e) => {
        if (nav.classList.contains('mobile-active') && !nav.contains(e.target) && !navToggle.contains(e.target)) {
            nav.classList.remove('mobile-active');
            navToggle.classList.remove('is-open');
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initIndexMobileNav);
} else {
    initIndexMobileNav();
}

function initIndexOperatorDropdown() {
    if (!document.body.classList.contains('page-index')) return;

    const dropdownPairs = [
        {
            trigger: document.getElementById('indexOperatorMenuBtn'),
            menu: document.getElementById('indexOperatorDropdown'),
        },
        {
            trigger: document.getElementById('indexOperatorMenuBtnMobile'),
            menu: document.getElementById('indexOperatorDropdownMobile'),
        },
    ].filter((pair) => pair.trigger && pair.menu);

    if (!dropdownPairs.length) return;

    document.addEventListener('click', (e) => {
        let handled = false;

        dropdownPairs.forEach(({ trigger, menu }) => {
            const clickedTrigger = e.target && typeof e.target.closest === 'function'
                ? e.target.closest(`#${trigger.id}`)
                : null;

            if (clickedTrigger) {
                e.stopPropagation();
                const isOpen = menu.classList.toggle('show');
                trigger.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
                handled = true;
            } else if (!menu.contains(e.target)) {
                menu.classList.remove('show');
                trigger.setAttribute('aria-expanded', 'false');
            }
        });

        if (handled) return;
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initIndexOperatorDropdown);
} else {
    initIndexOperatorDropdown();
}

// ---------- Cart Functions ----------
function addToCart(itemId) {
    const option = Array.from(productSelect.options).find(opt => opt.value === itemId);
    if (!option) return;

    const name = option.text.split(' ($')[0];
    const price = parseFloat(option.text.match(/\$(\d+(\.\d+)?)/)[1]);

    const existing = cart.find(i => i.id === itemId);
    if (existing) {
        existing.quantity += 1;
    } else {
        cart.push({ id: itemId, name, price, quantity: 1 });
    }

    renderCart();
}

function renderCart() {
    cartBody.innerHTML = '';
    if (cart.length === 0) {
        cartBody.innerHTML = `<tr><td colspan="4">Cart is empty. Select an item to start.</td></tr>`;
        cartTotal.innerText = '$0.00';
        return;
    }

    let total = 0;
    cart.forEach(item => {
        total += item.price * item.quantity;
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${item.name}</td>
            <td>$${item.price.toFixed(2)}</td>
            <td>
                <input type="number" min="1" value="${item.quantity}" data-id="${item.id}" class="qty-input">
            </td>
            <td><button class="btn-remove" data-id="${item.id}">Remove</button></td>
        `;
        cartBody.appendChild(row);
    });

    cartTotal.innerText = `$${total.toFixed(2)}`;

    // Re-attach listeners for dynamic elements
    document.querySelectorAll('.qty-input').forEach(input => {
        input.addEventListener('change', e => {
            const id = e.target.dataset.id;
            const qty = parseInt(e.target.value);
            const cartItem = cart.find(i => i.id === id);
            if (cartItem && qty > 0) {
                cartItem.quantity = qty;
                renderCart();
            }
        });
    });

    document.querySelectorAll('.btn-remove').forEach(btn => {
        btn.addEventListener('click', e => {
            const id = e.target.dataset.id;
            cart = cart.filter(i => i.id !== id);
            renderCart();
        });
    });
}

// ---------- Button Listeners ----------
if (addButton) {
    addButton.addEventListener('click', () => {
        const selectedId = productSelect.value;
        if (!selectedId) return;
        addToCart(selectedId);
    });
}

if (completeButton) {
    completeButton.addEventListener('click', () => {
        if (cart.length === 0) return alert('Cart is empty.');

        fetch('/sales/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({ cart })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                alert('Sale completed!');
                cart = [];
                renderCart();
                location.reload(); 
            } else {
                alert(data.error || 'Something went wrong.');
            }
        });
    });
}

// ---------- Barcode Scanner ----------
if (barcodeInput) {
    barcodeInput.addEventListener('keypress', e => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const scannedCode = barcodeInput.value.trim();
            barcodeInput.value = '';

            const option = Array.from(productSelect.options).find(opt => opt.value === scannedCode);
            if (option) {
                productSelect.value = option.value;
                addToCart(option.value);
            } else {
                alert('Item not found for barcode: ' + scannedCode);
            }
        }
    });
}

// ---------- CSRF Helper ----------
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

(function () {
    const OVERLAY_FLAG = "__qsErrorOverlayInitialized";
    if (window[OVERLAY_FLAG]) return;
    window[OVERLAY_FLAG] = true;

    const ensureOverlay = () => {
        if (document.getElementById("qsErrorOverlay")) {
            return document.getElementById("qsErrorOverlay");
        }
        if (!document.body) return null;

        const overlay = document.createElement("div");
        overlay.id = "qsErrorOverlay";
        overlay.className = "qs-error-overlay";
        overlay.setAttribute("hidden", "hidden");
        overlay.innerHTML = `
            <div class="qs-error-overlay__backdrop" data-qs-error-close="true"></div>
            <div class="qs-error-overlay__dialog" role="alertdialog" aria-modal="true" aria-labelledby="qsErrorOverlayTitle">
                <p class="qs-error-overlay__eyebrow">QuickStock detected a problem</p>
                <h2 id="qsErrorOverlayTitle" class="qs-error-overlay__title">Something went wrong</h2>
                <p class="qs-error-overlay__message" id="qsErrorOverlayMessage">An unexpected error interrupted this page.</p>
                <div class="qs-error-overlay__actions">
                    <button type="button" class="qs-error-overlay__button qs-error-overlay__button--primary" data-qs-error-reload="true">Reload page</button>
                    <button type="button" class="qs-error-overlay__button" data-qs-error-close="true">Dismiss</button>
                </div>
            </div>
        `;

        overlay.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof Element)) return;
            if (target.matches("[data-qs-error-close='true']")) {
                overlay.setAttribute("hidden", "hidden");
            }
            if (target.matches("[data-qs-error-reload='true']")) {
                window.location.reload();
            }
        });

        document.body.appendChild(overlay);
        return overlay;
    };

    const showOverlay = ({ title, message } = {}) => {
        const overlay = ensureOverlay();
        if (!overlay) return;
        const titleNode = overlay.querySelector("#qsErrorOverlayTitle");
        const messageNode = overlay.querySelector("#qsErrorOverlayMessage");
        if (titleNode) titleNode.textContent = title || "Something went wrong";
        if (messageNode) {
            messageNode.textContent = message || "An unexpected error interrupted this page. You can reload now or dismiss this notice and continue carefully.";
        }
        overlay.removeAttribute("hidden");
    };

    window.addEventListener("quickstock:error", (event) => {
        showOverlay(event.detail || {});
    });

    window.addEventListener("error", (event) => {
        showOverlay({
            title: "Page error detected",
            message: event.message || "An unexpected script error interrupted this page.",
        });
    });

    window.addEventListener("unhandledrejection", (event) => {
        const reason = event.reason;
        const message = typeof reason === "string"
            ? reason
            : reason && typeof reason.message === "string"
                ? reason.message
                : "An unexpected background task failed.";
        showOverlay({
            title: "Background action failed",
            message,
        });
    });

    if (typeof window.fetch === "function") {
        const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            try {
                const response = await originalFetch(...args);
                if (response.status >= 500) {
                    showOverlay({
                        title: "Server error detected",
                        message: `The server responded with error ${response.status}. Reload the page and try again.`,
                    });
                }
                return response;
            } catch (error) {
                showOverlay({
                    title: "Network connection problem",
                    message: error && error.message ? error.message : "QuickStock could not reach the server.",
                });
                throw error;
            }
        };
    }
})();
