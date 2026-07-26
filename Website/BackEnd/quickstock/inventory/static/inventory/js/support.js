document.addEventListener("DOMContentLoaded", () => {
    if (!document.body.classList.contains("page-support")) return;

    document.querySelectorAll(".accordion-header").forEach((header) => {
        header.addEventListener("click", () => {
            const item = header.closest(".accordion-item");
            const content = header.nextElementSibling;
            const icon = header.querySelector("span");
            if (!item || !content) return;

            const isOpen = item.classList.toggle("active");
            header.setAttribute("aria-expanded", isOpen ? "true" : "false");
            content.style.maxHeight = isOpen ? `${content.scrollHeight}px` : "";
            if (icon) icon.textContent = isOpen ? "−" : "+";
        });
    });
});
