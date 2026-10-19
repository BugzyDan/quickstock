
  (function () {
      const pref = "{{ request.user.profile.theme|default:'system' }}";
      const root = document.documentElement;
      function applyTheme(mode) { root.setAttribute("data-theme-applied", mode); }
      if (pref === "system") {
          const mq = window.matchMedia("(prefers-color-scheme: dark)");
          applyTheme(mq.matches ? "dark" : "light");
          if (typeof mq.addEventListener === "function") {
              mq.addEventListener("change", (e) => applyTheme(e.matches ? "dark" : "light"));
          } else if (typeof mq.addListener === "function") {
              mq.addListener((e) => applyTheme(e.matches ? "dark" : "light"));
          }
      } else {
          applyTheme(pref);
      }
  })();
  