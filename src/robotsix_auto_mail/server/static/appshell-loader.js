// appshell-loader.js — CSP-safe AppShell bootstrap
//
// Replaces the bespoke board-header with the shared @robotsix/ui
// AppShell that every fleet UI uses.  The server renders a config
// element (a hidden <div>) carrying the brand, nav items, settings
// href, and right-slot HTML; this module reads it, dynamic-imports
// the vendored robotsix-ui.js, and mounts the shell.
//
// The config element uses a <div> (not a <script>) so a strict CSP
// (script-src-elem) does not flag a violation on every page load.

(function () {
  "use strict";

  var configEl = document.getElementById("appshell-config");
  var config = configEl
    ? JSON.parse(configEl.getAttribute("data-appshell-config") || "{}")
    : {};

  import("/static/robotsix-ui.js")
    .then(function (ui) {
      // Build a right-slot node from the server-rendered HTML so it
      // renders as DOM rather than textContent (mountAppShell treats
      // a string rightSlot as plain text).
      var rightSlotNode = null;
      if (config.rightSlotHTML) {
        var tmp = document.createElement("span");
        tmp.innerHTML = config.rightSlotHTML;
        rightSlotNode = tmp;
      }

      var result = ui.mountAppShell(
        document.getElementById("app-shell"),
        {
          brand: config.brand || "",
          navItems: config.navItems || [],
          settingsHref: config.settingsHref || "",
          rightSlot: rightSlotNode,
        },
      );

      // After mounting, re-attach event listeners for any
      // dynamically-injected elements inside the right slot.
      // The AppShell replaces container.innerHTML, so a fresh
      // delegation pass is not needed — the existing capture-phase
      // listeners on document body already cover the new elements.
      var shellEl = result.element;
      if (shellEl) {
        shellEl.setAttribute("data-appshell-mounted", "");
      }
    })
    .catch(function () {
      // Degrade gracefully: show a fallback that preserves the
      // page content even when the static asset is missing.
      var shell = document.getElementById("app-shell");
      if (shell) {
        shell.innerHTML =
          '<header class="board-header" role="banner">' +
          "<h1>" +
          (config.brand ? config.brand : "Mail Board") +
          "</h1>" +
          "</header>";
      }
    });
})();