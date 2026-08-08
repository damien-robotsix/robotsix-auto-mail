// board-events.js — CSP-safe event delegation layer.
//
// Replaces all inline onclick/onsubmit/onchange handlers with
// addEventListener-based delegation.  Loaded after board-auto-mail.js
// so it can reference window.closeDetail, window.refreshBoard, and
// window.authorizeAccount.
//
// All event listeners are registered via capture-phase delegation on
// document.body — no inline handlers, no 'unsafe-inline' needed.

(function () {
  "use strict";

  /* ==================================================================
   * 1.  probeHealth — recheck connections (replaces inline onclick)
   * ================================================================ */

  function probeHealth() {
    fetch("/probe-health")
      .then(function () {
        window.location.reload();
      })
      .catch(function () {
        window.location.reload();
      });
  }

  /* ==================================================================
   * 2.  Confirm-dialog helpers
   * ================================================================ */

  // Returns the confirm message from data-confirm attribute, or the
  // provided default if the attribute is absent.
  function confirmMessage(el, fallback) {
    var msg = el.getAttribute("data-confirm");
    return msg || fallback;
  }

  /* ==================================================================
   * 3.  Delegated event listeners (capture phase)
   * ================================================================ */

  document.addEventListener(
    "click",
    function (e) {
      // ---- 3a.  Close button (side-panel) ----
      var closeBtn = e.target.closest(".side-panel .close-btn");
      if (closeBtn && typeof window.closeDetail === "function") {
        e.preventDefault();
        window.closeDetail();
        return;
      }

      // ---- 3b.  Probe health button ----
      var probeBtn = e.target.closest("#probe-health-btn");
      if (probeBtn) {
        e.preventDefault();
        probeHealth();
        return;
      }

      // ---- 3c.  Authorize button (OAuth2) ----
      var authBtn = e.target.closest(".auth-btn");
      if (authBtn && typeof window.authorizeAccount === "function") {
        e.preventDefault();
        window.authorizeAccount(authBtn);
        return;
      }

      // ---- 3d.  Browse button (archive folder tree) ----
      // The browse button is already handled by board-auto-mail.js's
      // own click delegation — we just prevent default and let it
      // propagate.  No action needed here.
      var browseBtn = e.target.closest(".archive-browse-btn");
      if (browseBtn) {
        // board-auto-mail.js handles this — just ensure no default.
        e.preventDefault();
        return;
      }
    },
    true,
  ); // capture phase

  /* ==================================================================
   * 4.  Change delegation
   * ================================================================ */

  document.addEventListener(
    "change",
    function (e) {
      // ---- 4a.  Account picker ----
      var picker = e.target.closest("#account-picker");
      if (picker) {
        var value = picker.value;
        window.location.href =
          "/board?account=" + encodeURIComponent(value);
      }
    },
    true,
  ); // capture phase

  /* ==================================================================
   * 5.  Submit delegation (confirm dialogs)
   * ================================================================ */

  document.addEventListener(
    "submit",
    function (e) {
      var form = e.target;

      // ---- 5a.  Delete form (single) ----
      if (
        form.classList.contains("delete-form") &&
        !form.action.includes("/batch-delete")
      ) {
        var msg = confirmMessage(
          form,
          "Permanently delete this mail from mailbox and database?",
        );
        if (!confirm(msg)) {
          e.preventDefault();
          return;
        }
      }

      // ---- 5b.  Batch delete form ----
      if (
        form.classList.contains("delete-form") &&
        form.action.includes("/batch-delete")
      ) {
        var msg = confirmMessage(
          form,
          "Permanently delete ALL mail in this column " +
            "from mailbox and database?",
        );
        if (!confirm(msg)) {
          e.preventDefault();
          return;
        }
      }

      // ---- 5c.  Archive confirm form (single) ----
      if (form.classList.contains("archive-confirm-form")) {
        // Read the destination path from the sibling .archive-path element
        var proposal = form.closest(".archive-proposal");
        var pathEl = proposal
          ? proposal.querySelector(".archive-path")
          : null;
        var path = pathEl ? pathEl.textContent : "(unknown)";
        var msg = confirmMessage(
          form,
          "Archive this mail to " + path + "?",
        );
        if (!confirm(msg)) {
          e.preventDefault();
          return;
        }
      }

      // ---- 5d.  Archive All form ----
      if (form.classList.contains("archive-form")) {
        var msg = confirmMessage(
          form,
          "Archive ALL mail in this column to their proposed folders?",
        );
        if (!confirm(msg)) {
          e.preventDefault();
          return;
        }
      }

      // ---- 5e.  Force triage form ----
      if (form.classList.contains("force-triage-form")) {
        // Read count from the form's inline data or from the column
        var label = form.getAttribute("data-label") || "";
        var count = form.getAttribute("data-count") || "";
        var msg = confirmMessage(form, "");
        if (!msg) {
          msg =
            "Re-triage all " +
            (count || "") +
            " items in " +
            (label || "this column") +
            "?";
        }
        if (!confirm(msg)) {
          e.preventDefault();
          return;
        }
      }

      // ---- 5f.  Archive group form (per-destination batch) ----
      if (form.classList.contains("archive-group-form")) {
        // Read the destination label from the sibling .archive-group-label
        var group = form.closest(".archive-group");
        var labelEl = group
          ? group.querySelector(".archive-group-label")
          : null;
        var labelTxt = labelEl ? labelEl.textContent : "(unknown)";
        var msg = confirmMessage(
          form,
          "Archive " + labelTxt + "?",
        );
        if (!confirm(msg)) {
          e.preventDefault();
          return;
        }
      }
    },
    true,
  ); // capture phase
})();
