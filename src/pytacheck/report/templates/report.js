/* pytacheck report: paginated tables, tabsets and the colour-scheme toggle. */
(function () {
  "use strict";

  function pageButton(label, page, current, disabled) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.dataset.page = String(page);
    if (page === current && /^\d+$/.test(label)) b.className = "current";
    if (disabled) b.disabled = true;
    return b;
  }

  function pageList(current, pages) {
    // DataTables "simple_numbers": at most 7 entries with ellipses
    if (pages <= 7) {
      var all = [];
      for (var i = 0; i < pages; i++) all.push(i);
      return all;
    }
    if (current < 4) return [0, 1, 2, 3, 4, -1, pages - 1];
    if (current > pages - 5) return [0, -1, pages - 5, pages - 4, pages - 3, pages - 2, pages - 1];
    return [0, -1, current - 1, current, current + 1, -1, pages - 1];
  }

  function setupTable(wrap) {
    var size = parseInt(wrap.getAttribute("data-page-length"), 10);
    var rows = wrap.querySelectorAll("tbody > tr");
    var nav = wrap.querySelector(".dt-paging");
    if (!size || size < 1 || !nav || rows.length <= size) return;
    var pages = Math.ceil(rows.length / size);
    function show(page) {
      for (var i = 0; i < rows.length; i++) {
        rows[i].style.display = (i >= page * size && i < (page + 1) * size) ? "" : "none";
      }
      nav.textContent = "";
      nav.appendChild(pageButton("Previous", page - 1, page, page === 0));
      pageList(page, pages).forEach(function (p) {
        if (p < 0) {
          var s = document.createElement("span");
          s.className = "ellipsis";
          s.textContent = "…";
          nav.appendChild(s);
        } else {
          nav.appendChild(pageButton(String(p + 1), p, page, false));
        }
      });
      nav.appendChild(pageButton("Next", page + 1, page, page === pages - 1));
    }
    nav.addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.tagName === "BUTTON" && !t.disabled) show(parseInt(t.dataset.page, 10));
    });
    show(0);
  }

  function setupTabset(set) {
    var buttons = set.querySelectorAll(":scope > .tab-nav > .tab-btn");
    var panes = set.querySelectorAll(":scope > .tab-pane");
    buttons.forEach(function (btn, i) {
      btn.addEventListener("click", function () {
        buttons.forEach(function (b, j) { b.classList.toggle("active", i === j); });
        panes.forEach(function (p, j) { p.classList.toggle("active", i === j); });
      });
    });
  }

  function setupTheme() {
    var root = document.documentElement;
    var toggle = document.getElementById("theme-toggle");
    var saved = null;
    try { saved = window.localStorage.getItem("pytacheck-theme"); } catch (e) { saved = null; }
    if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
    if (!toggle) return;
    toggle.addEventListener("click", function () {
      var current = root.getAttribute("data-theme");
      if (!current) {
        current = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      }
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { window.localStorage.setItem("pytacheck-theme", next); } catch (e) { /* storage unavailable */ }
    });
  }

  function init() {
    document.querySelectorAll(".datatables[data-page-length]").forEach(setupTable);
    document.querySelectorAll(".panel-tabset").forEach(setupTabset);
    setupTheme();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
