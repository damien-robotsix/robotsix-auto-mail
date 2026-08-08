// add-account-redirect.js — CSP-safe parent-frame redirect
//
// The add-account embed page (served inside the settings iframe) cannot
// use an inline <script>window.top.location.href=…</script> because a
// strict Content-Security-Policy (no 'unsafe-inline') blocks it.  Instead,
// the server sets a data-redirect attribute on <body> and loads this file.
// On load, we read the attribute and redirect the parent frame.

(function () {
  "use strict";

  var target = document.body && document.body.dataset.redirect;
  if (target) {
    window.top.location.href = target;
  }
})();
