/*
 * board-auto-mail.js — app-specific overlay for the Mail Board.
 *
 * Composes on top of robotsix-board's board.js public API, following
 * the same overlay pattern as mill's board-mill.js.  Reads custom
 * configuration from the #board-config <script> element emitted by
 * the Python server.
 *
 * Responsibilities:
 *   • openDetail / closeDetail — iframe-based side-panel
 *   • capture-phase card-click interceptor (overrides board.js drawer)
 *   • hash-based navigation + Escape-key handler
 *   • Board auto-refresh polling (HTML-replacement model)
 */

(function () {
  "use strict";

  /* ==================================================================
   * 0.  Configuration (from #board-config)
   * ================================================================ */

  var CFG = null;

  function bootConfig() {
    var el = document.getElementById("board-config");
    if (!el) return false;
    try {
      CFG = JSON.parse(el.textContent || "{}");
    } catch (_err) {
      return false;
    }
    return true;
  }

  if (!bootConfig()) {
    // Page not configured for auto-mail — bail out.
    return;
  }

  var accountQs = CFG.account_qs || "";
  var fetchQs = CFG.fetch_qs || "";
  var dataAccountJs = CFG.data_account_js === true;

  /* ==================================================================
   * 1.  Side-panel (iframe-based, replaces board.js's #drawer)
   * ================================================================ */

  function openDetail(messageId, subject, focusDraft, cardAccount) {
    var src = "/email/" + messageId + "?embed=1";
    if (cardAccount && dataAccountJs) {
      src += "&account=" + cardAccount;
    } else if (!dataAccountJs && accountQs) {
      src += accountQs;
    }
    if (focusDraft) src += "&draft=1";

    var panel = document.querySelector(".side-panel");
    if (!panel) return;
    panel.querySelector("iframe").src = src;
    panel.classList.add("open");
    var wrapper = document.querySelector(".board-wrapper");
    if (wrapper) wrapper.classList.add("panel-open");
    var titleEl = document.querySelector(".panel-title");
    if (titleEl) titleEl.textContent = subject || "";
    location.hash = messageId;
  }

  function closeDetail() {
    var panel = document.querySelector(".side-panel");
    if (panel) {
      panel.classList.remove("open");
      panel.querySelector("iframe").src = "";
    }
    var wrapper = document.querySelector(".board-wrapper");
    if (wrapper) wrapper.classList.remove("panel-open");
    location.hash = "";
  }

  /* ==================================================================
   * 2.  Card-click interceptor (capture phase)
   * ================================================================ */

  function attachCardClickInterceptor() {
    var board = document.querySelector(".board");
    if (!board) return;

    board.addEventListener("click", function (e) {
      // Ignore clicks on interactive elements inside cards.
      if (e.target.closest("button, select, input")) return;

      var card = e.target.closest(".board-card");
      if (!card) return;

      var meta = card.querySelector(".card-extra");
      var mid = meta && meta.getAttribute("data-message-id");
      if (!mid) return;

      if (e.target.closest("form")) return;

      e.preventDefault();
      e.stopPropagation(); // prevent board.js's drawer delegation

      var subject = (meta && meta.getAttribute("data-subject")) || "";
      if (dataAccountJs) {
        var cardAccount = (meta && meta.getAttribute("data-account")) || "";
        openDetail(mid, subject, false, cardAccount);
      } else {
        openDetail(mid, subject, false);
      }
    }, true); // capture phase — fires before board.js's bubble handler
  }

  /* ==================================================================
   * 2b. Per-destination archive groups (TO_ARCHIVE column)
   *
   * The server sorts TO_ARCHIVE cards by destination and tags each with
   * data-archive-dest, so contiguous runs are whole groups.  We inject a
   * header (folder + count + "Archive these" button) before each run.  The
   * button posts the destination to /batch-archive-folder, archiving just
   * that group.  Re-run after every board refresh (idempotent).
   * ================================================================ */

  function buildArchiveGroupHeader(dest, count) {
    var label = dest || "Archive root";
    var wrap = document.createElement("div");
    wrap.className = "archive-group";

    var lbl = document.createElement("span");
    lbl.className = "archive-group-label";
    lbl.textContent = "📁 " + label + " (" + count + ")";
    wrap.appendChild(lbl);

    var form = document.createElement("form");
    form.className = "archive-group-form";
    form.method = "post";
    form.action = "/batch-archive-folder" + fetchQs;
    form.onsubmit = function () {
      return confirm("Archive " + count + " mail to " + label + "?");
    };
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "folder";
    input.value = dest; // .value is safe — no HTML injection
    form.appendChild(input);
    var btn = document.createElement("button");
    btn.type = "submit";
    btn.className = "archive-btn archive-group-btn";
    btn.textContent = "Archive these " + count + " →";
    form.appendChild(btn);
    wrap.appendChild(form);
    return wrap;
  }

  function renderArchiveGroups() {
    // Clear any headers from a previous render (idempotent on refresh).
    var old = document.querySelectorAll(".archive-group");
    for (var i = 0; i < old.length; i++) old[i].remove();

    // The aggregate ("All mailboxes") view mixes accounts; per-folder batch
    // archive targets a single account, so skip grouping there — mirroring the
    // server suppressing "Archive All" in aggregate mode.
    if (dataAccountJs) return;

    var extras = document.querySelectorAll(".card-extra[data-archive-dest]");
    var groups = [];
    var cur = null;
    for (var j = 0; j < extras.length; j++) {
      var dest = extras[j].getAttribute("data-archive-dest") || "";
      var card = extras[j].closest(".board-card");
      if (!card) continue;
      if (!cur || cur.dest !== dest) {
        cur = { dest: dest, firstCard: card, count: 0 };
        groups.push(cur);
      }
      cur.count += 1;
    }
    for (var k = 0; k < groups.length; k++) {
      var g = groups[k];
      var header = buildArchiveGroupHeader(g.dest, g.count);
      g.firstCard.parentNode.insertBefore(header, g.firstCard);
    }
  }

  /* ==================================================================
   * 3.  Hash routing + Escape key
   * ================================================================ */

  function attachHashRouting() {
    if (location.hash) {
      var mid = location.hash.slice(1);
      if (mid) {
        // Open the detail for the hashed message.  We don't know the
        // subject at this point (the card might not even be rendered
        // yet), but the server-rendered board cards carry data-subject.
        var card = document.querySelector(
          '.card-extra[data-message-id="' + CSS.escape(mid) + '"]'
        );
        var subject = "";
        if (card) {
          subject = card.getAttribute("data-subject") || "";
        }
        if (dataAccountJs) {
          var cardAccount = card ? (card.getAttribute("data-account") || "") : "";
          openDetail(mid, subject, false, cardAccount);
        } else {
          openDetail(mid, subject, false);
        }
      }
    }

    window.addEventListener("hashchange", function () {
      if (!location.hash) closeDetail();
    });
  }

  function attachEscapeKey() {
    window.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeDetail();
    });
  }

  /* ==================================================================
   * 4.  Board auto-refresh polling
   * ================================================================ */

  var refreshTimer = null;

  function refreshBoard(force) {
    var sidePanel = document.getElementById("side-panel");
    if (!force && sidePanel && sidePanel.classList.contains("open")) return;

    var savedX = window.pageXOffset;
    var savedY = window.pageYOffset;
    var prevBoard = document.querySelector(".board");
    var savedBoardLeft = prevBoard ? prevBoard.scrollLeft : 0;
    var savedBoardTop = prevBoard ? prevBoard.scrollTop : 0;

    fetch("/board-content" + fetchQs)
      .then(function (r) {
        if (!r.ok) throw new Error("bad status");
        return r.json();
      })
      .then(function (data) {
        var board = document.querySelector(".board");
        if (board) board.innerHTML = data.columns_html;
        renderArchiveGroups();

        var proposals = document.querySelector(".rule-proposals");
        if (proposals) proposals.outerHTML = data.proposals_html;

        var tc = document.getElementById("triage-control");
        if (tc) {
          if (data.triage_running) {
            tc.innerHTML =
              '<div class="triage-banner">Triage is currently running. ' +
              "The board will refresh automatically.</div>";
          } else {
            tc.innerHTML = "";
          }
        }

        var bc = document.getElementById("batch-control");
        if (bc) {
          var op = data.batch_op;
          if (op) {
            var verb = op.op === "archive" ? "Archiving" : "Deleting";
            var prog =
              typeof op.done === "number" && typeof op.total === "number"
                ? ": " + op.done + "/" + op.total
                : "";
            bc.innerHTML =
              '<div class="batch-banner">' +
              verb +
              " mail" +
              prog +
              ". The board will refresh automatically.</div>";
          } else {
            bc.innerHTML = "";
          }
        }

        window.scrollTo(savedX, savedY);
        var newBoard = document.querySelector(".board");
        if (newBoard) {
          newBoard.scrollLeft = savedBoardLeft;
          newBoard.scrollTop = savedBoardTop;
        }
      })
      .catch(function () {
        /* silently retry next cycle */
      });
  }

  function startRefreshLoop() {
    refreshTimer = setInterval(refreshBoard, 30000);
  }

  /* ==================================================================
   * 5.  Bootstrap
   * ================================================================ */

  function init() {
    attachCardClickInterceptor();
    renderArchiveGroups();
    attachHashRouting();
    attachEscapeKey();
    startRefreshLoop();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // -- Expose public API on window ------------------------------------
  window.closeDetail = closeDetail;
  window.refreshBoard = refreshBoard;
})();
