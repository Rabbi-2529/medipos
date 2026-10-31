(function () {
  "use strict";

  var root = document.documentElement;

  function setTheme(theme) {
    root.setAttribute("data-bs-theme", theme);
    try { localStorage.setItem("medipos-theme", theme); } catch (e) { /* storage blocked */ }
  }

  // ---- Ctrl+K menu search -------------------------------------------------
  var paletteEl = document.getElementById("commandPalette");
  var dataEl = document.getElementById("nav-search-data");
  var entries = dataEl ? JSON.parse(dataEl.textContent) : [];
  var palette = paletteEl && window.bootstrap ? new bootstrap.Modal(paletteEl) : null;
  var input = paletteEl && paletteEl.querySelector("[data-command-input]");
  var results = paletteEl && paletteEl.querySelector("[data-command-results]");
  var empty = paletteEl && paletteEl.querySelector("[data-command-empty]");
  var activeIndex = 0;

  function renderResults() {
    var q = input.value.trim().toLowerCase();
    var matches = entries.filter(function (e) {
      return !q || (e.label + " " + e.group).toLowerCase().indexOf(q) !== -1;
    });
    results.replaceChildren();
    matches.forEach(function (e, i) {
      var a = document.createElement("a");
      a.className = "command-item" + (i === activeIndex ? " is-active" : "");
      a.href = e.url;
      a.setAttribute("role", "option");
      var label = document.createElement("span");
      label.textContent = e.label;
      a.appendChild(label);
      if (e.group) {
        var group = document.createElement("small");
        group.textContent = e.group;
        a.appendChild(group);
      }
      results.appendChild(a);
    });
    empty.classList.toggle("d-none", matches.length > 0);
  }

  function openPalette() {
    if (!palette) return;
    input.value = "";
    activeIndex = 0;
    renderResults();
    palette.show();
  }

  if (paletteEl) {
    paletteEl.addEventListener("shown.bs.modal", function () { input.focus(); });
    input.addEventListener("input", function () { activeIndex = 0; renderResults(); });
    input.addEventListener("keydown", function (ev) {
      var items = results.querySelectorAll(".command-item");
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        if (!items.length) return;
        activeIndex = (activeIndex + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach(function (el, i) { el.classList.toggle("is-active", i === activeIndex); });
        items[activeIndex].scrollIntoView({ block: "nearest" });
      } else if (ev.key === "Enter" && items[activeIndex]) {
        ev.preventDefault();
        window.location.href = items[activeIndex].getAttribute("href");
      }
    });
  }

  document.addEventListener("keydown", function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault();
      openPalette();
    }
  });

  // ---- Click actions ------------------------------------------------------
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (t.closest("[data-theme-toggle]")) {
      setTheme(root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
    } else if (t.closest("[data-sidebar-toggle]")) {
      document.body.classList.toggle("sidebar-open");
    } else if (t.closest("[data-sidebar-close]")) {
      document.body.classList.remove("sidebar-open");
    } else if (t.closest("[data-command-open]")) {
      openPalette();
    } else if (t.closest("[data-toggle-password]")) {
      var btn = t.closest("[data-toggle-password]");
      var field = document.getElementById(btn.getAttribute("data-toggle-password"));
      var show = field.type === "password";
      field.type = show ? "text" : "password";
      btn.querySelector(".bi").className = "bi " + (show ? "bi-eye-slash" : "bi-eye");
    }
  });

  // ---- HTMX: send the CSRF token with every request ------------------------
  document.body.addEventListener("htmx:configRequest", function (ev) {
    var meta = document.querySelector("meta[name=csrf-token]");
    if (meta) ev.detail.headers["X-CSRFToken"] = meta.content;
  });

  // ---- Shared widgets (Select2, DataTables) --------------------------------
  var MP = window.MP = window.MP || {};

  // <select data-mp-select [data-no-search] [data-allow-clear] [data-placeholder="…"]>
  // (Not "data-select2": jQuery would expose it as .data("select2"), the key Select2 keeps its instance under.)
  MP.initSelect2 = function (root) {
    if (!window.jQuery || !jQuery.fn.select2) return;
    jQuery(root || document).find("select[data-mp-select]").each(function () {
      var $el = jQuery(this);
      if ($el.hasClass("select2-hidden-accessible")) return;
      var $modal = $el.closest(".modal");
      $el.select2({
        theme: "bootstrap-5",
        width: "100%",
        placeholder: $el.data("placeholder") || "",
        allowClear: $el.is("[data-allow-clear]"),
        minimumResultsForSearch: $el.is("[data-no-search]") ? Infinity : 0,
        dropdownParent: $modal.length ? $modal : jQuery(document.body)
      });
    });
  };

  // Server-side DataTable with the app's defaults; pass DataTables options to override.
  MP.dataTable = function (selector, options) {
    return new DataTable(selector, Object.assign({
      processing: true,
      serverSide: true,
      pageLength: 25,
      lengthMenu: [10, 25, 50, 100],
      searchDelay: 350,
      language: {
        search: "",
        searchPlaceholder: "Search…",
        lengthMenu: "_MENU_ per page",
        info: "_START_–_END_ of _TOTAL_",
        infoEmpty: "No records",
        infoFiltered: "(filtered from _MAX_)",
        zeroRecords: "Nothing matches your search.",
        emptyTable: "No records yet.",
        processing: '<span class="spinner-border spinner-border-sm text-primary" role="status"></span>'
      }
    }, options || {}));
  };

  MP.initSelect2();
  document.body.addEventListener("htmx:afterSwap", function (ev) { MP.initSelect2(ev.target); });
})();
