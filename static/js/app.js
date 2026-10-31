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

  // Client-side DataTable for short lists rendered in the page: <table data-mp-table>.
  // Columns whose <th> has class "no-sort" aren't sortable.
  MP.initLocalTables = function (root) {
    if (!window.DataTable) return;
    (root || document).querySelectorAll("table[data-mp-table]").forEach(function (el) {
      if (el.dataset.mpReady) return;
      el.dataset.mpReady = "1";
      var rows = el.tBodies[0] ? el.tBodies[0].rows.length : 0;
      new DataTable(el, {
        paging: rows > 25,
        pageLength: 25,
        info: rows > 25,
        order: [],
        columnDefs: [{ targets: "no-sort", orderable: false, searchable: false }],
        language: {
          search: "", searchPlaceholder: "Search…", lengthMenu: "_MENU_ per page",
          info: "_START_–_END_ of _TOTAL_", zeroRecords: "Nothing matches your search.", emptyTable: "Nothing here yet."
        }
      });
    });
  };

  // <form data-confirm="Are you sure?">: ask in a styled dialog before submitting.
  var confirmModal;
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.matches("form[data-confirm]") || form.dataset.confirmed) return;
    ev.preventDefault();
    if (!confirmModal) {
      var wrap = document.createElement("div");
      wrap.className = "modal fade";
      wrap.tabIndex = -1;
      wrap.innerHTML =
        '<div class="modal-dialog modal-dialog-centered modal-sm"><div class="modal-content">' +
        '<div class="modal-body pt-4"><p class="mb-0" data-confirm-text></p></div>' +
        '<div class="modal-footer border-0"><button type="button" class="btn btn-link" data-bs-dismiss="modal">Cancel</button>' +
        '<button type="button" class="btn btn-danger" data-confirm-ok>Yes, continue</button></div></div></div>';
      document.body.appendChild(wrap);
      confirmModal = { el: wrap, modal: new bootstrap.Modal(wrap) };
    }
    confirmModal.el.querySelector("[data-confirm-text]").textContent = form.dataset.confirm;
    confirmModal.el.querySelector("[data-confirm-ok]").onclick = function () {
      form.dataset.confirmed = "1";
      confirmModal.modal.hide();
      form.requestSubmit ? form.requestSubmit() : form.submit();
    };
    confirmModal.modal.show();
  });

  MP.initSelect2();
  MP.initLocalTables();
  document.body.addEventListener("htmx:afterSwap", function (ev) {
    MP.initSelect2(ev.target);
    MP.initLocalTables(ev.target);
  });
})();
