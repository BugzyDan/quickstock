
        (function () {
            const rates = {
                JM: "{{ tax_rate_percent_by_country.JM|floatformat:2 }}",
                TT: "{{ tax_rate_percent_by_country.TT|floatformat:2 }}",
                BB: "{{ tax_rate_percent_by_country.BB|floatformat:2 }}",
                GY: "{{ tax_rate_percent_by_country.GY|floatformat:2 }}",
                LC: "{{ tax_rate_percent_by_country.LC|floatformat:2 }}",
                INT: "{{ tax_rate_percent_by_country.INT|floatformat:2 }}"
            };
            const countrySelect = document.getElementById("tax-country");
            const rateInput = document.getElementById("tax-rate-input");

            function syncRate() {
                const code = countrySelect.value;
                if (rates[code] !== undefined) {
                    rateInput.value = rates[code];
                }
            }

            if (countrySelect && rateInput) {
                countrySelect.addEventListener("change", syncRate);
                syncRate();
            }
        })();
    