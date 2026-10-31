(function () {
  "use strict";

  var root = document.documentElement;
  // Words for tables and dialogs, translated by the server (see core.context_processors.js_texts).
  var textsEl = document.getElementById("mp-i18n");
  var T = Object.assign({
    search: "Search…", perPage: "_MENU_ per page", info: "_START_–_END_ of _TOTAL_", infoEmpty: "No records",
    infoFiltered: "(filtered from _MAX_)", zeroRecords: "Nothing matches your search.", emptyTable: "No records yet.",
    nothingHere: "Nothing here yet.", cancel: "Cancel", confirm: "Yes, continue", searching: "Searching…",
    noResults: "No results found", loadingMore: "Loading more…", typeMore: "Type to search"
  }, textsEl ? JSON.parse(textsEl.textContent) : {});

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
    } else if (t.closest("[data-fullscreen-toggle]")) {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (root.requestFullscreen) root.requestFullscreen().catch(function () {});
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
  MP.t = T;
  MP.select2Language = {
    searching: function () { return T.searching; },
    noResults: function () { return T.noResults; },
    loadingMore: function () { return T.loadingMore; },
    inputTooShort: function () { return T.typeMore; }
  };
  MP.tableLanguage = {
    search: "", searchPlaceholder: T.search, lengthMenu: T.perPage, info: T.info, infoEmpty: T.infoEmpty,
    infoFiltered: T.infoFiltered, zeroRecords: T.zeroRecords, emptyTable: T.emptyTable,
    processing: '<span class="spinner-border spinner-border-sm text-primary" role="status"></span>'
  };

  // <select data-mp-select [data-no-search] [data-allow-clear] [data-placeholder="…"]>
  // (Not "data-select2": jQuery would expose it as .data("select2"), the key Select2 keeps its instance under.)
  MP.initSelect2 = function (root) {
    if (!window.jQuery || !jQuery.fn.select2) return;
    jQuery(root || document).find("select[data-mp-select]").each(function () {
      var $el = jQuery(this);
      if ($el.hasClass("select2-hidden-accessible")) return;
      var $modal = $el.closest(".modal");
      var options = {
        theme: "bootstrap-5",
        width: "100%",
        placeholder: $el.data("placeholder") || "",
        allowClear: $el.is("[data-allow-clear]"),
        minimumResultsForSearch: $el.is("[data-no-search]") ? Infinity : 0,
        dropdownParent: $modal.length ? $modal : jQuery(document.body),
        tags: $el.is("[data-tags]"),  // lets people type a value that isn't in the list yet
        language: MP.select2Language
      };
      // data-ajax-url="…": search the server as you type ({results, pagination} JSON).
      if ($el.data("ajaxUrl")) {
        options.ajax = {
          url: $el.data("ajaxUrl"),
          dataType: "json",
          delay: 250,
          data: function (params) { return { q: params.term || "", page: params.page || 1 }; },
          cache: true
        };
      }
      $el.select2(options);
    });
  };

  // Server-side DataTable with the app's defaults; pass DataTables options to override.
  MP.dataTable = function (selector, options) {
    options = options || {};
    // Page-specific language bits (e.g. a search placeholder) are layered on the translated defaults.
    var language = Object.assign({}, MP.tableLanguage, options.language || {});
    return new DataTable(selector, Object.assign({
      processing: true,
      serverSide: true,
      pageLength: 25,
      lengthMenu: [10, 25, 50, 100],
      searchDelay: 350
    }, options, { language: language }));
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
        language: Object.assign({}, MP.tableLanguage, { emptyTable: T.nothingHere })
      });
    });
  };

  // Date and month fields: a themed flatpickr calendar instead of the browser's own.
  // The real <input> keeps sending Y-m-d (or Y-m) to the server; people see the pharmacy's date format.
  // Add data-native to an input to keep the browser picker.
  if (window.flatpickr && flatpickr.l10ns && flatpickr.l10ns.bn && root.lang.indexOf("bn") === 0) flatpickr.localize(flatpickr.l10ns.bn);

  function pickerFooter(instance) {
    var bar = document.createElement("div");
    bar.className = "mp-picker-footer";
    [[T.today, function () { instance.setDate(new Date(), true); instance.close(); }],
     [T.clear, function () { instance.clear(); instance.close(); }]].forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button"; btn.textContent = b[0];
      btn.addEventListener("click", b[1]);
      bar.appendChild(btn);
    });
    instance.calendarContainer.appendChild(bar);
  }

  MP.initDatePickers = function (scope) {
    if (!window.flatpickr) return;
    var format = root.getAttribute("data-date-format") || "d M Y";
    (scope || document).querySelectorAll("input[type=date]:not([data-native]), input[type=month]:not([data-native])").forEach(function (el) {
      if (el._flatpickr) return;
      var month = el.type === "month";
      var label = el.id ? document.querySelector("label[for='" + el.id + "']") : null;
      var options = {
        altInput: true,
        altInputClass: el.className + " mp-date-input",
        altFormat: month ? "M Y" : format,
        dateFormat: month ? "Y-m" : "Y-m-d",
        allowInput: true,
        disableMobile: true,
        static: !!el.closest(".modal"),  // inside a dialog the calendar must live in the dialog too
        minDate: el.min || null,
        maxDate: el.max || null,
        plugins: month && window.monthSelectPlugin ? [new monthSelectPlugin({ shorthand: true, dateFormat: "Y-m", altFormat: "M Y" })] : [],
        onReady: function (_d, _s, instance) {
          var alt = instance.altInput;
          alt.placeholder = el.placeholder || (month ? "" : T.pickDate);
          if (el.getAttribute("aria-label")) alt.setAttribute("aria-label", el.getAttribute("aria-label"));
          else if (label) alt.setAttribute("aria-label", label.textContent.trim());
          if (label) label.addEventListener("click", function (ev) { ev.preventDefault(); alt.focus(); instance.open(); });
          pickerFooter(instance);
        },
        // Scripts that watch the field for "input" (e.g. purchase lines) hear about picked dates too.
        onChange: function () { el.dispatchEvent(new Event("input", { bubbles: true })); }
      };
      flatpickr(el, options);
    });
  };

  // Call before throwing away rows that hold pickers, so their calendars don't pile up in <body>.
  MP.destroyDatePickers = function (scope) {
    (scope || document).querySelectorAll("input").forEach(function (el) { if (el._flatpickr) el._flatpickr.destroy(); });
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
        '<div class="modal-footer border-0"><button type="button" class="btn btn-link" data-bs-dismiss="modal" data-confirm-cancel></button>' +
        '<button type="button" class="btn btn-danger" data-confirm-ok></button></div></div></div>';
      wrap.querySelector("[data-confirm-cancel]").textContent = T.cancel;
      wrap.querySelector("[data-confirm-ok]").textContent = T.confirm;
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
  MP.initDatePickers();
  document.body.addEventListener("htmx:afterSwap", function (ev) {
    MP.initSelect2(ev.target);
    MP.initLocalTables(ev.target);
    MP.initDatePickers(ev.target);
  });
})();
