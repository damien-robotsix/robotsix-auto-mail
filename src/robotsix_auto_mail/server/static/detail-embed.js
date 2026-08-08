// detail-embed.js — parent-refresh for the email-detail embed iframe.
//
// The embed fragment (detail.py _build_detail_html, embed branch) loads
// this script so the parent board refreshes after a detail-panel action
// (move, save draft, send reply, etc.).  CSP-safe — no inline handlers.

(function () {
  "use strict";

  if (
    window.parent &&
    window.parent !== window &&
    typeof window.parent.refreshBoard === "function"
  ) {
    window.parent.refreshBoard(true);
  }
})();
