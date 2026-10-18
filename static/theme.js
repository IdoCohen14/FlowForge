"use strict";
// Apply the saved palette before styles load. Storage may be blocked by the browser.
(() => {
  let theme = "dark";
  try { if (localStorage.getItem("flowforge-theme") === "light") theme = "light"; } catch (_) {}
  document.documentElement.dataset.theme = theme;
})();
