// settings-loader.js — CSP-safe Settings panel bootstrap
//
// The Settings page cannot use an inline <script type="module"> because
// a strict Content-Security-Policy (no 'unsafe-inline') blocks it.
// Instead, the server loads this external module, which dynamic-imports
// the vendored robotsix-ui.js and mounts the config panel.

import("/static/robotsix-ui.js")
  .then((ui) => {
    ui.mountConfigPanel(document.getElementById("settings-panel"), {
      title: "Settings",
    });
  })
  .catch(() => {
    document.getElementById("settings-panel").innerHTML =
      '<p class="panel-fallback">The shared config panel asset is missing. ' +
      "It is vendored at image build time; for a local checkout run " +
      "<code>scripts/vendor-ui.sh</code>.</p>";
  });
