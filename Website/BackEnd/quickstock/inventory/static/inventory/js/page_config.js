(function () {
    const configs = {
        quickstockDashboardConfig: "DashboardConfig",
        quickstockTerminalConfig: "TerminalConfig",
        quickstockAnalyticsConfig: "AnalyticsConfig",
        quickstockCashRegisterConfig: "QuickStockConfig"
    };

    Object.entries(configs).forEach(([id, windowKey]) => {
        if (window[windowKey]) return;
        const node = document.getElementById(id);
        if (!node) return;

        try {
            window[windowKey] = JSON.parse(node.textContent || "{}");
        } catch (error) {
            console.error(`Unable to parse ${id}`, error);
            window[windowKey] = {};
        }
    });
})();
