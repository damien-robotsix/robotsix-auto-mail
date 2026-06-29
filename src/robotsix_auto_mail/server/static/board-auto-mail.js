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

  /* ----- shared fetch helper -------------------------------------- */

  function fetchJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("bad status");
      return r.json();
    });
  }

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
   * 2c. Folder-tree browser (Browse button → popover)
   *
   * Fetches /archive-folders once on page load and caches the flat list.
   * The flat list is parsed into a nested tree (split on "/").
   * A click on any ".archive-browse-btn" opens a positioned popover with
   * the expandable tree; clicking a leaf fills the sibling text input and
   * closes the popover.  Suppressed in aggregate (dataAccountJs) mode.
   * ================================================================ */

  var folderCache = null; // null = not fetched yet, [] = empty
  var folderPopover = null;
  var folderBrowseBtn = null;

  function fetchFolders() {
    if (folderCache !== null) return;
    // Suppress in aggregate mode — the server also omits the button, but
    // guard defensively.
    if (dataAccountJs) {
      folderCache = [];
      hideBrowseButtons();
      return;
    }
    fetchJson("/archive-folders" + fetchQs)
      .then(function (data) {
        folderCache = data.folders || [];
        if (folderCache.length === 0) {
          hideBrowseButtons();
        }
      })
      .catch(function () {
        folderCache = [];
        hideBrowseButtons();
      });
  }

  function hideBrowseButtons() {
    var btns = document.querySelectorAll(".archive-browse-btn");
    for (var i = 0; i < btns.length; i++) {
      btns[i].style.display = "none";
    }
  }

  function buildFolderTree(folders) {
    var root = { _children: {} };
    folders.forEach(function (path) {
      var parts = path.split("/");
      var node = root;
      for (var i = 0; i < parts.length; i++) {
        var part = parts[i];
        if (!node._children[part]) {
          node._children[part] = { _children: {} };
        }
        node = node._children[part];
      }
      node._leaf = true;
    });
    return root;
  }

  function renderTree(name, node, depth, fullPath) {
    var children = Object.keys(node._children);
    var hasChildren = children.length > 0;
    var isLeaf = node._leaf && !hasChildren;

    var row = document.createElement("div");
    row.className = "ft-node";
    row.style.paddingLeft = (depth * 1.2 + 0.3) + "em";
    row.setAttribute("data-ft-path", fullPath);

    if (hasChildren) {
      var toggle = document.createElement("span");
      toggle.className = "ft-toggle";
      toggle.textContent = "\u25b6"; // ▶

      var label = document.createElement("span");
      label.textContent = "\u{1F4C1} " + name;
      if (node._leaf) {
        label.className = "ft-branch ft-branch-leaf";
        label.addEventListener("click", function (e) {
          e.stopPropagation();
          selectArchiveFolder(fullPath);
        });
      } else {
        label.className = "ft-branch";
      }

      var childContainer = document.createElement("div");
      childContainer.className = "ft-children";
      childContainer.style.display = "none";

      children.sort().forEach(function (childName) {
        var childNode = node._children[childName];
        var childFullPath = fullPath ? fullPath + "/" + childName : childName;
        childContainer.appendChild(
          renderTree(childName, childNode, depth + 1, childFullPath)
        );
      });

      toggle.addEventListener("click", function (e) {
        e.stopPropagation();
        var collapsed = childContainer.style.display === "none";
        childContainer.style.display = collapsed ? "block" : "none";
        toggle.textContent = collapsed ? "\u25bc" : "\u25b6"; // ▼ / ▶
      });

      row.appendChild(toggle);
      row.appendChild(label);
      row.appendChild(childContainer);
    } else if (isLeaf) {
      var leafLabel = document.createElement("span");
      leafLabel.className = "ft-leaf";
      leafLabel.textContent = "\u{1F4C4} " + name;
      leafLabel.addEventListener("click", function (e) {
        e.stopPropagation();
        selectArchiveFolder(fullPath);
      });
      row.appendChild(leafLabel);
    } else {
      // Empty intermediate node (should not happen with the current
      // data model, but handle gracefully).
      var emptyLabel = document.createElement("span");
      emptyLabel.className = "ft-branch";
      emptyLabel.textContent = "\u{1F4C1} " + name;
      row.appendChild(emptyLabel);
    }

    return row;
  }

  function selectArchiveFolder(path) {
    if (!folderBrowseBtn) return;
    var mid = folderBrowseBtn.getAttribute("data-message-id");
    var card = document.querySelector(
      '.card-extra[data-message-id="' + CSS.escape(mid) + '"]'
    );
    if (!card) return;
    var form = card.querySelector(".archive-override-form");
    if (!form) return;
    var input = form.querySelector('input[name="subfolder"]');
    if (!input) return;
    input.value = path;
    closeFolderPopover();
    // Focus the Set button so the user can immediately confirm.
    var setBtn = form.querySelector('button[type="submit"]');
    if (setBtn) setBtn.focus();
  }

  function openFolderPopover(btn) {
    closeFolderPopover();
    if (!folderCache || folderCache.length === 0) return;

    folderBrowseBtn = btn;

    var popover = document.createElement("div");
    popover.className = "folder-tree-popover";

    var tree = buildFolderTree(folderCache);
    var rootKeys = Object.keys(tree._children).sort();
    for (var i = 0; i < rootKeys.length; i++) {
      var name = rootKeys[i];
      var node = tree._children[name];
      popover.appendChild(renderTree(name, node, 0, name));
    }
    if (rootKeys.length === 0) {
      var emptyMsg = document.createElement("div");
      emptyMsg.className = "ft-empty";
      emptyMsg.textContent = "(no subfolders)";
      popover.appendChild(emptyMsg);
    }

    document.body.appendChild(popover);
    folderPopover = popover;

    positionFolderPopover(btn, popover);

    // Close on next outside click (deferred so the current click
    // doesn't immediately close it).
    setTimeout(function () {
      document.addEventListener("click", folderOutsideClick, true);
    }, 0);
  }

  function positionFolderPopover(btn, popover) {
    var rect = btn.getBoundingClientRect();
    var scrollX = window.pageXOffset;
    var scrollY = window.pageYOffset;

    popover.style.position = "absolute";
    popover.style.top = (rect.bottom + scrollY + 4) + "px";
    popover.style.left = (rect.left + scrollX) + "px";

    // Nudge into the viewport if it overflows on the right or bottom.
    var popRect = popover.getBoundingClientRect();
    if (popRect.right > window.innerWidth - 8) {
      var adjLeft = window.innerWidth - popRect.width - 8 + scrollX;
      if (adjLeft < 8) adjLeft = 8;
      popover.style.left = adjLeft + "px";
    }
    if (popRect.bottom > window.innerHeight - 8) {
      var adjTop = rect.top + scrollY - popRect.height - 4;
      if (adjTop < 8) adjTop = 8;
      popover.style.top = adjTop + "px";
    }
  }

  function closeFolderPopover() {
    if (folderPopover) {
      folderPopover.remove();
      folderPopover = null;
      document.removeEventListener("click", folderOutsideClick, true);
    }
    folderBrowseBtn = null;
  }

  function folderOutsideClick(e) {
    if (
      folderPopover &&
      !folderPopover.contains(e.target) &&
      (!folderBrowseBtn || !folderBrowseBtn.contains(e.target))
    ) {
      closeFolderPopover();
    }
  }

  // Escape key closes the popover (appended to the existing Escape handler
  // below in section 3).

  // Delegate click on Browse buttons.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".archive-browse-btn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    if (folderPopover && folderBrowseBtn === btn) {
      closeFolderPopover();
    } else {
      // Re-fetch in case the cache was never populated (e.g. fetch failed
      // silently earlier).
      if (folderCache === null) {
        fetch("/archive-folders" + fetchQs)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            folderCache = data.folders || [];
            if (folderCache.length) openFolderPopover(btn);
          })
          .catch(function () {});
        return;
      }
      openFolderPopover(btn);
    }
  });

  // Kick off the folder fetch on load.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fetchFolders);
  } else {
    fetchFolders();
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
      if (e.key === "Escape") {
        // Close the folder-tree popover first (if open); otherwise
        // close the detail panel.
        if (folderPopover) {
          closeFolderPopover();
        } else {
          closeDetail();
        }
      }
    });
  }

  /* ==================================================================
   * 4.  Board auto-refresh polling
   * ================================================================ */

  var refreshTimer = null;

  function refreshBoard(force) {
    // Close the folder-tree popover if open — the card HTML it
    // references will be replaced.
    if (folderPopover) closeFolderPopover();

    var sidePanel = document.getElementById("side-panel");
    if (!force && sidePanel && sidePanel.classList.contains("open")) return;

    var savedX = window.pageXOffset;
    var savedY = window.pageYOffset;
    var prevBoard = document.querySelector(".board");
    var savedBoardLeft = prevBoard ? prevBoard.scrollLeft : 0;
    var savedBoardTop = prevBoard ? prevBoard.scrollTop : 0;

    fetchJson("/board-content" + fetchQs)
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
            var verbLabels = CFG.batch_op_verb_labels;
            var verb = verbLabels[op.op] || "Processing";
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

        var ha = document.getElementById("health-alerts");
        if (ha) {
          ha.innerHTML = data.health_alerts_html || "";
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
