(() => {
  "use strict";

  const storageKey = "outdoor-maps-theme";
  const root = document.documentElement;
  const systemPreference = window.matchMedia("(prefers-color-scheme: dark)");

  function storedTheme() {
    try {
      const value = window.localStorage.getItem(storageKey);
      return value === "light" || value === "dark" ? value : null;
    } catch {
      return null;
    }
  }

  function updateToggle(theme) {
    const toggle = document.getElementById("theme-toggle");
    if (!toggle) return;
    const isDark = theme === "dark";
    const nextTheme = isDark ? "light" : "dark";
    toggle.setAttribute("aria-pressed", String(isDark));
    toggle.setAttribute("aria-label", `Switch to ${nextTheme} theme`);
    toggle.title = `Switch to ${nextTheme} theme`;
    const label = toggle.querySelector(".theme-toggle__label");
    if (label) label.textContent = isDark ? "Light" : "Dark";
  }

  function applyTheme(theme) {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.content = theme === "dark" ? "#0c2420" : "#18372f";
    updateToggle(theme);
  }

  function saveTheme(theme) {
    try {
      window.localStorage.setItem(storageKey, theme);
    } catch {
      // Theme selection still works when storage is unavailable.
    }
  }

  applyTheme(storedTheme() || (systemPreference.matches ? "dark" : "light"));

  function bindThemeToggle() {
    updateToggle(root.dataset.theme);
    document.getElementById("theme-toggle")?.addEventListener("click", () => {
      const theme = root.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(theme);
      saveTheme(theme);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindThemeToggle, { once: true });
  } else {
    bindThemeToggle();
  }

  systemPreference.addEventListener?.("change", (event) => {
    if (!storedTheme()) applyTheme(event.matches ? "dark" : "light");
  });
})();
