// Run on all pages
document.addEventListener('DOMContentLoaded', () => {
    loadNavigation(); // Load the navigation menu
    checkUserRole();  // Hide admin links if user is not admin
});

function loadNavigation() {
    const navHTML = `
        <ul>
            <li><a href="dashboard.html">Dashboard</a></li>
            <li><a href="inventory.html" class="admin-only">Inventory</a></li>
            <li><a href="cash_register.html">Cash Register</a></li>
        </ul>
    `;
    const navElement = document.querySelector('nav');
    if (navElement) navElement.innerHTML = navHTML;
}

function checkUserRole() {
    // Logic to check if the user is an admin or staff
    // Hides links if user is not an admin (e.g., hiding Inventory)
}
