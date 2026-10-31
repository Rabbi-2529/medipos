/* Line editor for write-offs, transfers and stock counts: pick a medicine, then enter quantities per batch.
   mode "out": how many units leave each batch (never more than on hand).
   mode "count": what was counted on the shelf per batch; new batches found can be added. */
(function () {
  "use strict";

  var box = document.querySelector("[data-stock-lines]");
  if (!box) return;
  var mode = box.dataset.mode;
  var T = JSON.parse(document.getElementById("lines-i18n").textContent);
  var initial = JSON.parse(document.getElementById("lines-initial").textContent);
  var initialNew = JSON.parse(document.getElementById("lines-initial-new").textContent);
  var currency = box.dataset.currency || "";
  var outletField = document.querySelector(box.dataset.outletField);
  var form = box.closest("form");
  var tbody = box.querySelector("[data-lines]"), empty = box.querySelector("[data-empty]"), summary = box.querySelector("[data-summary]");
  var groups = [];  // [{medicine, batches, values: {batchId: "n"}, extra: [{batch_no, expiry, counted}]}]

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function num(v) { var n = parseInt(v, 10); return isNaN(n) ? 0 : n; }
  function money(v) { return (v < 0 ? "−" : "") + currency + Math.abs(Number(v)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function fmt(text, params) { return text.replace(/%\((\w+)\)s/g, function (_m, k) { return params[k]; }); }
  function outlet() { return outletField ? outletField.value : ""; }

  function fetchBatches(medicineId) {
    return fetch(box.dataset.batchesUrl + "?outlet=" + encodeURIComponent(outlet()) + "&medicine=" + medicineId)
      .then(function (r) { if (!r.ok) throw new Error(T.error); return r.json(); });
  }

  function addGroup(medicineId, values, extra) {
    if (!outlet()) { window.alert(T.pickOutlet); return Promise.resolve(); }
    var existing = groups.find(function (g) { return g.medicine.id === Number(medicineId); });
    if (existing) { Object.assign(existing.values, values || {}); render(); return Promise.resolve(); }
    return fetchBatches(medicineId).then(function (data) {
      groups.push({ medicine: data.medicine, batches: data.batches, values: values || {}, extra: extra || [] });
      render();
    });
  }

  function input(attrs, value, onInput) {
    var i = el("input", "form-control form-control-sm");
    Object.keys(attrs).forEach(function (k) { i.setAttribute(k, attrs[k]); });
    i.value = value == null ? "" : value;
    i.addEventListener("input", function () { onInput(i.value); updateSummary(); });
    return i;
  }

  function batchCells(tr, b) {
    var batch = el("td"); batch.appendChild(el("span", "fw-semibold", b.batchNo));
    tr.appendChild(batch);
    var exp = el("td");
    if (b.expiry) {
      exp.appendChild(el("span", b.expired ? "text-danger fw-semibold" : "", b.expiry));
      if (b.expired) { exp.appendChild(document.createTextNode(" ")); exp.appendChild(el("span", "badge text-bg-danger", T.expired)); }
    } else exp.textContent = "—";
    tr.appendChild(exp);
  }

  function render() {
    MP.destroyDatePickers(tbody);
    tbody.replaceChildren();
    empty.classList.toggle("d-none", groups.length > 0);
    groups.forEach(function (g, gi) {
      var head = el("tr", "lines-group");
      var title = el("td"); title.colSpan = mode === "count" ? 5 : 4;
      title.appendChild(el("span", "fw-semibold", g.medicine.name));
      var remove = el("td", "text-end");
      var x = el("button", "btn btn-sm btn-link text-danger p-0"); x.type = "button"; x.setAttribute("aria-label", T.remove);
      x.innerHTML = '<i class="bi bi-x-lg"></i>';
      x.addEventListener("click", function () { groups.splice(gi, 1); render(); });
      remove.appendChild(x);
      head.append(title, remove);
      tbody.appendChild(head);

      var rows = mode === "out" ? g.batches.filter(function (b) { return b.onHand > 0; }) : g.batches;
      if (!rows.length && !(mode === "count")) {
        var none = el("tr"); var td = el("td", "text-body-secondary small", T.noStock); td.colSpan = 5; none.appendChild(td);
        tbody.appendChild(none);
      }
      rows.forEach(function (b) {
        var tr = el("tr");
        batchCells(tr, b);
        tr.appendChild(el("td", "text-center", b.onHand + " " + g.medicine.unit));
        if (mode === "out") {
          var cell = el("td");
          cell.appendChild(input({ type: "number", min: 0, max: b.onHand, step: 1, "aria-label": T.qty }, g.values[b.id], function (v) {
            g.values[b.id] = v;
          }));
          tr.append(cell, el("td"));
        } else {
          var counted = el("td");
          var diff = el("td", "text-center fw-semibold");
          var show = function (v) {
            var d = v === "" ? null : num(v) - b.onHand;
            diff.textContent = d === null ? "" : (d > 0 ? "+" + d : String(d));
            diff.className = "text-center fw-semibold " + (d > 0 ? "text-success" : d < 0 ? "text-danger" : "text-body-secondary");
          };
          counted.appendChild(input({ type: "number", min: 0, step: 1, "aria-label": T.counted }, g.values[b.id], function (v) {
            g.values[b.id] = v; show(v);
          }));
          show(g.values[b.id] == null ? "" : String(g.values[b.id]));
          tr.append(counted, diff, el("td"));
        }
        tbody.appendChild(tr);
      });

      if (mode === "count") {
        g.extra.forEach(function (n, ni) {
          var tr = el("tr", "lines-new");
          var c1 = el("td"); c1.appendChild(input({ maxlength: 40, placeholder: T.batchNo, "aria-label": T.batchNo }, n.batch_no, function (v) { n.batch_no = v; }));
          var c2 = el("td"); c2.appendChild(input({ type: "month", "aria-label": T.expiry }, n.expiry, function (v) { n.expiry = v; }));
          var c3 = el("td", "text-center text-body-secondary", "0");
          var c4 = el("td"); c4.appendChild(input({ type: "number", min: 0, step: 1, "aria-label": T.counted }, n.counted, function (v) { n.counted = v; }));
          var c5 = el("td", "text-end");
          var rm = el("button", "btn btn-sm btn-link text-danger p-0"); rm.type = "button"; rm.setAttribute("aria-label", T.remove);
          rm.innerHTML = '<i class="bi bi-dash-circle"></i>';
          rm.addEventListener("click", function () { g.extra.splice(ni, 1); render(); });
          c5.appendChild(rm);
          tr.append(c1, c2, c3, c4, el("td"), c5);
          tbody.appendChild(tr);
        });
        var addRow = el("tr");
        var addCell = el("td"); addCell.colSpan = 6;
        var add = el("button", "btn btn-sm btn-link p-0", "+ " + T.addBatch); add.type = "button";
        add.addEventListener("click", function () { g.extra.push({ batch_no: "", expiry: "", counted: "" }); render(); });
        addCell.appendChild(add); addRow.appendChild(addCell);
        tbody.appendChild(addRow);
      }
    });
    MP.initDatePickers(tbody);
    updateSummary();
  }

  function updateSummary() {
    var units = 0, value = 0;
    groups.forEach(function (g) {
      g.batches.forEach(function (b) {
        var v = g.values[b.id];
        if (v === undefined || v === "") return;
        var delta = mode === "out" ? num(v) : num(v) - b.onHand;
        units += delta; value += delta * Number(b.unitCost);
      });
    });
    if (!summary) return;
    summary.textContent = groups.length ? fmt(mode === "out" ? T.outSummary : T.countSummary, {
      units: (mode === "count" && units > 0 ? "+" : "") + units, value: money(value)
    }) : "";
  }

  // Changing the outlet changes every batch's stock: reload them and start the quantities over.
  if (outletField) {
    var previous = outlet();
    var reload = function () {
      if (outlet() === previous) return;
      if (groups.length && !window.confirm(T.changeOutlet)) { outletField.value = previous; if (window.jQuery) jQuery(outletField).trigger("change.select2"); return; }
      previous = outlet();
      var ids = groups.map(function (g) { return g.medicine.id; });
      groups = [];
      ids.reduce(function (p, id) { return p.then(function () { return addGroup(id); }); }, Promise.resolve()).then(render);
    };
    outletField.addEventListener("change", reload);
    if (window.jQuery) jQuery(outletField).on("change", reload);
  }

  if (window.jQuery) {
    jQuery("#add-line-medicine").on("select2:select", function (ev) {
      addGroup(ev.params.data.id).catch(function (err) { window.alert(err.message); });
      jQuery(this).val(null).trigger("change");
    });
  }

  form.addEventListener("submit", function () {
    var lines = [], extra = [];
    groups.forEach(function (g) {
      g.batches.forEach(function (b) {
        var v = g.values[b.id];
        if (v === undefined || v === "") return;
        var line = { medicine: g.medicine.id, name: g.medicine.name, batch: b.id };
        line[mode === "out" ? "qty" : "counted"] = v;
        lines.push(line);
      });
      g.extra.forEach(function (n) {
        if (n.counted !== "") extra.push({ medicine: g.medicine.id, name: g.medicine.name, batch_no: n.batch_no, expiry: n.expiry, counted: n.counted });
      });
    });
    form.querySelector("[name=lines]").value = JSON.stringify(lines);
    var extraField = form.querySelector("[name=new_batches]");
    if (extraField) extraField.value = JSON.stringify(extra);
  });

  // Lines sent back after an error, or one line handed over by the expiring-stock report.
  var wanted = {};
  initial.forEach(function (l) {
    var id = Number(l.medicine);
    wanted[id] = wanted[id] || { values: {}, extra: [] };
    wanted[id].values[l.batch] = l.qty != null ? l.qty : l.counted;
  });
  initialNew.forEach(function (n) {
    var id = Number(n.medicine);
    wanted[id] = wanted[id] || { values: {}, extra: [] };
    wanted[id].extra.push({ batch_no: n.batch_no || "", expiry: n.expiry || "", counted: n.counted });
  });
  Object.keys(wanted).reduce(function (p, id) {
    return p.then(function () { return addGroup(id, wanted[id].values, wanted[id].extra); });
  }, Promise.resolve()).then(render);
})();
