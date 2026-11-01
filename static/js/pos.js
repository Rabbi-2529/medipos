/* ThingBD MediPOS — point of sale. The server re-checks everything; this keeps the counter fast. */
(function () {
  "use strict";

  var cfg = JSON.parse(document.getElementById("pos-config").textContent);
  var T = JSON.parse(document.getElementById("pos-i18n").textContent);
  var $ = window.jQuery;
  var csrf = (document.querySelector("meta[name=csrf-token]") || {}).content;

  var state = { lines: [], customer: null, priceType: "retail", discount: "", discountMode: "amount", quotationId: null, points: "" };
  var results = [], activeResult = 0, searchTimer = null, searchSeq = 0;
  // Before anything is typed the grid shows a category ("top" = best sellers); arrow keys there only pick once used.
  var browse = { category: "top", hasSales: true, more: false, navigated: false };

  // ---- helpers ------------------------------------------------------------------
  function q(sel, root) { return (root || document).querySelector(sel); }
  function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }
  function round2(v) { return Math.round((v + Number.EPSILON) * 100) / 100; }
  function money(v) { return cfg.currency + Number(v).toLocaleString("en-US", { minimumFractionDigits: cfg.decimals, maximumFractionDigits: cfg.decimals }); }
  function fmt(text, params) { return text.replace(/%\((\w+)\)s/g, function (_m, k) { return params[k]; }); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function modal(id) { return bootstrap.Modal.getOrCreateInstance(document.getElementById(id)); }
  function toast(text) { var t = q("[data-toast]"); q(".toast-body", t).textContent = text; bootstrap.Toast.getOrCreateInstance(t, { delay: 2500 }).show(); }

  function api(url, body) {
    var opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) };
    return fetch(url, opts).catch(function () {
      setOnline(false);
      var lost = new Error(T.offline); lost.offline = true; throw lost;
    }).then(function (r) {
      setOnline(true);
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || T.error);
        return data;
      });
    });
  }

  // ---- without internet (static/js/pos_offline.js) ----------------------------------
  var offline = window.MPOffline && window.indexedDB ? window.MPOffline : null, online = navigator.onLine;
  function setOnline(yes) {
    online = yes;
    var badge = q("[data-net]");
    if (badge) { badge.classList.toggle("is-offline", !yes); q("[data-net-label]").textContent = yes ? T.online : T.offlineShort; }
  }
  /* Ask the server; when it can't be reached, answer from the till's own copy. */
  function orOffline(url, local) {
    return api(url).catch(function (err) {
      if (err.offline && offline && offline.hasCopy()) return local();
      throw err;
    });
  }

  // ---- pricing ------------------------------------------------------------------
  function unitOf(line) { return line.units.find(function (u) { return u.key === line.unit; }) || line.units[0]; }
  function unitPrice(line) { return state.priceType === "wholesale" && line.wholesale != null ? num(line.wholesale) : num(line.price); }
  function gross(line) { return round2(unitPrice(line) * unitOf(line).factor * line.qty); }
  function lineTotal(line) { return Math.max(0, gross(line) - num(line.discount)); }
  // A running promotion fills the line discount by itself until the cashier types their own.
  function promoDiscount(line) {
    var p = line.promo;
    if (!p) return 0;
    var units = unitOf(line).factor * line.qty;
    if (units < p.minQty) return 0;
    var g = gross(line);
    var off = p.kind === "percent" ? g * num(p.value) / 100 : num(p.value) * units;
    return round2(Math.min(off, g));
  }
  function applyPromos() {
    state.lines.forEach(function (l) {
      if (l.promo && l.autoDiscount !== false) { var d = promoDiscount(l); l.discount = d ? String(d) : ""; }
    });
  }
  function promoText(p) { return p.kind === "percent" ? "−" + Number(p.value) + "%" : "−" + money(p.value); }
  function unitsOf(medicineId) {
    return state.lines.reduce(function (sum, l) { return l.medicine === medicineId ? sum + unitOf(l).factor * l.qty : sum; }, 0);
  }

  function finish(after, discount) {
    var taxable = round2(after - discount);
    var vat = round2(taxable * num(cfg.vatPercent) / 100);
    var exact = round2(taxable + vat);
    return { vat: vat, exact: exact, total: cfg.roundTotal ? Math.round(exact) : exact };
  }
  // Loyalty: the most points the picked customer may spend on a bill (same rule as apps.messaging.loyalty.most_points).
  function mostPoints(bill) {
    var L = cfg.loyalty, c = state.customer;
    if (!L || !L.enabled || !c || !num(L.pointValue) || num(c.points) < Math.max(L.minRedeem, 1)) return 0;
    var most = Math.max(0, Math.min(num(c.points), Math.floor(bill * L.maxPercent / 100 / num(L.pointValue) + 1e-9)));
    return most >= L.minRedeem ? most : 0;  // a bill too small to spend the minimum on
  }
  function totals() {
    var subtotal = 0, lineDiscount = 0;
    state.lines.forEach(function (l) { subtotal += gross(l); lineDiscount += Math.min(num(l.discount), gross(l)); });
    var after = round2(subtotal - lineDiscount);
    var discount = state.discountMode === "percent" ? round2(after * num(state.discount) / 100) : num(state.discount);
    discount = Math.min(discount, after);
    var before = finish(after, discount), most = mostPoints(before.total);
    var points = Math.min(Math.floor(num(state.points)), most);
    if (points < (cfg.loyalty ? cfg.loyalty.minRedeem : 0)) points = 0;
    var pointsValue = points ? round2(points * num(cfg.loyalty.pointValue)) : 0;
    var done = points ? finish(after, Math.min(round2(discount + pointsValue), after)) : before;
    return { subtotal: subtotal, lineDiscount: lineDiscount, discount: discount, points: points, pointsValue: pointsValue, mostPoints: most,
             vat: done.vat, rounding: round2(done.total - done.exact), total: done.total };
  }

  function cartLines() {
    return state.lines.map(function (l) {
      return { medicine: l.medicine, unit: l.unit, qty: l.qty, discount: num(l.discount), batch: l.batch,
               promotion: l.promo && l.autoDiscount !== false && num(l.discount) ? l.promo.id : null };
    });
  }

  // ---- search and browse --------------------------------------------------------
  var search = q("#pos-search"), resultsBox = q("[data-results]"), catsBox = q("[data-cats]");

  function runSearch(term, addExact) {
    var seq = ++searchSeq;
    return orOffline(cfg.urls.search + "?q=" + encodeURIComponent(term), function () { return offline.search(term); }).then(function (data) {
      if (seq !== searchSeq) return;
      results = data.results; activeResult = 0;
      if (addExact && data.exact && results.length) { addItem(results[0]); search.value = ""; showBrowse(); return; }
      renderResults(term);
    });
  }

  function showBrowse(category) {
    if (category) browse.category = category;
    catsBox.querySelectorAll("[data-category]").forEach(function (b) { b.classList.toggle("is-active", b.dataset.category === browse.category); });
    var seq = ++searchSeq;
    return orOffline(cfg.urls.search + "?category=" + encodeURIComponent(browse.category), function () { return offline.browse(browse.category); }).then(function (data) {
      if (seq !== searchSeq) return;
      results = data.results; activeResult = 0;
      browse.hasSales = data.hasSales; browse.more = data.more; browse.navigated = false;
      renderResults("");
    }).catch(function (err) { toast(err.message); });
  }

  function renderResults(term) {
    resultsBox.replaceChildren();
    catsBox.classList.toggle("is-searching", !!term);
    if (!results.length) { resultsBox.appendChild(el("p", "pos-hint", term ? T.noResults : T.emptyCategory)); return; }
    if (!term && browse.category === "top" && !browse.hasSales) resultsBox.appendChild(el("p", "pos-note", T.noSalesYet));
    var showActive = !!term || browse.navigated;
    results.forEach(function (m, i) {
      var card = el("button", "pos-result tone-" + m.tone + (showActive && i === activeResult ? " is-active" : "") + (m.stock ? "" : " is-empty"));
      card.type = "button";
      var head = el("div", "pos-result-head");
      var icon = el("span", "pos-result-icon"); icon.appendChild(el("i", "bi " + m.icon));
      head.append(icon, el("span", "fw-semibold pos-result-name", m.name));
      if (m.promo) { var tag = el("span", "pos-offer", promoText(m.promo)); tag.title = m.promo.name; head.appendChild(tag); }
      if (m.rx) head.appendChild(el("span", "badge text-bg-danger", "Rx"));
      card.appendChild(head);
      card.appendChild(el("small", "text-body-secondary d-block", [m.generic, m.maker].filter(Boolean).join(" · ")));
      var foot = el("div", "pos-result-foot");
      foot.appendChild(el("span", m.stock ? "text-success" : "text-danger", m.stock ? m.stock + " " + T.inStock : T.outOfStock));
      if (m.nearestExpiry) foot.appendChild(el("span", "text-body-secondary", T.expires + " " + m.nearestExpiry.slice(0, 7)));
      foot.appendChild(el("strong", "", money(state.priceType === "wholesale" && m.wholesale ? m.wholesale : m.price)));
      card.appendChild(foot);
      card.addEventListener("click", function () { addItem(m); search.focus(); });
      resultsBox.appendChild(card);
    });
    if (!term && browse.more) resultsBox.appendChild(el("p", "pos-note", fmt(T.showingFirst, { n: results.length })));
    var active = resultsBox.querySelector(".is-active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  catsBox.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-category]");
    if (!btn) return;
    clearTimeout(searchTimer);
    search.value = "";
    showBrowse(btn.dataset.category);
    search.focus();
  });

  search.addEventListener("input", function () {
    clearTimeout(searchTimer);
    var term = search.value.trim();
    if (!term) { showBrowse(); return; }
    searchTimer = setTimeout(function () { runSearch(term, false); }, 180);
  });

  search.addEventListener("keydown", function (ev) {
    var term = search.value.trim();
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (!results.length) return;
      if (!term && !browse.navigated) { browse.navigated = true; activeResult = ev.key === "ArrowDown" ? 0 : results.length - 1; }
      else activeResult = (activeResult + (ev.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
      renderResults(term);
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      if (!term) {
        // Empty box: Enter only adds a card the cashier picked with the arrow keys, never by accident.
        if (browse.navigated && results[activeResult]) addItem(results[activeResult]);
        return;
      }
      clearTimeout(searchTimer);
      // A scanner types fast and presses Enter: look up now and add an exact barcode/code match.
      runSearch(term, true).then(function () {
        if (results.length && search.value.trim() === term) { addItem(results[activeResult]); search.value = ""; showBrowse(); }
      });
    }
  });

  // ---- cart ---------------------------------------------------------------------
  function addItem(m) {
    if (!m.stock) { toast(T.outOfStock + ": " + m.name); return; }
    if (unitsOf(m.id) + 1 > m.stock) { toast(fmt(T.onlyInStock, { n: m.stock })); return; }
    var existing = state.lines.find(function (l) { return l.medicine === m.id && l.unit === "piece" && !l.batch; });
    if (existing) { existing.qty += 1; }
    else {
      state.lines.push({ medicine: m.id, name: m.name, rx: m.rx, price: m.price, wholesale: m.wholesale, units: m.units,
                         stock: m.stock, unit: "piece", qty: 1, discount: "", batch: null, batchLabel: "", promo: m.promo || null });
    }
    render();
  }

  var cartBody = q("[data-cart]");

  function render() {
    applyPromos();
    cartBody.replaceChildren();
    q("[data-cart-empty]").classList.toggle("d-none", state.lines.length > 0);
    state.lines.forEach(function (line, i) {
      var tr = document.createElement("tr");

      var name = el("td");
      var title = el("div", "fw-semibold pos-line-name", line.name);
      if (line.rx) { title.appendChild(document.createTextNode(" ")); title.appendChild(el("span", "badge text-bg-danger", "Rx")); }
      name.appendChild(title);
      if (line.promo && line.autoDiscount !== false && num(line.discount)) {
        name.appendChild(el("small", "pos-line-offer", T.promo + ": " + line.promo.name));
      }
      var batchLink = el("button", "btn btn-link btn-sm p-0 pos-batch-link", line.batch ? T.batch + " " + line.batchLabel : T.chooseBatch);
      batchLink.type = "button";
      batchLink.addEventListener("click", function () { chooseBatch(line); });
      name.appendChild(batchLink);
      tr.appendChild(name);

      var unitCell = el("td");
      var unit = el("select", "form-select form-select-sm");
      line.units.forEach(function (u) { var o = el("option", "", u.label); o.value = u.key; o.selected = u.key === line.unit; unit.appendChild(o); });
      unit.disabled = line.units.length < 2;
      unit.addEventListener("change", function () {
        var old = line.unit; line.unit = unit.value;
        if (unitsOf(line.medicine) > line.stock) { line.unit = old; unit.value = old; toast(fmt(T.onlyInStock, { n: line.stock })); }
        render();
      });
      unitCell.appendChild(unit);
      tr.appendChild(unitCell);

      var qtyCell = el("td");
      var group = el("div", "qty-stepper");
      var minus = el("button", "qty-btn qty-minus"); minus.type = "button"; minus.setAttribute("aria-label", T.less);
      minus.appendChild(el("i", "bi bi-dash-lg")); minus.disabled = line.qty <= 1;
      var qty = el("input"); qty.type = "number"; qty.min = 1; qty.value = line.qty; qty.setAttribute("aria-label", T.qty);
      var plus = el("button", "qty-btn qty-plus"); plus.type = "button"; plus.setAttribute("aria-label", T.more);
      plus.appendChild(el("i", "bi bi-plus-lg"));
      function setQty(n) {
        n = Math.max(1, Math.floor(n) || 1);
        var other = unitsOf(line.medicine) - unitOf(line).factor * line.qty;
        if (other + unitOf(line).factor * n > line.stock) { toast(fmt(T.onlyInStock, { n: line.stock })); n = line.qty; }
        line.qty = n; render();
      }
      minus.addEventListener("click", function () { setQty(line.qty - 1); });
      plus.addEventListener("click", function () { setQty(line.qty + 1); });
      qty.addEventListener("change", function () { setQty(num(qty.value)); });
      group.append(minus, qty, plus);
      qtyCell.appendChild(group);
      tr.appendChild(qtyCell);

      var discCell = el("td");
      var disc = el("input", "form-control form-control-sm"); disc.type = "number"; disc.min = 0; disc.step = "0.01"; disc.value = line.discount;
      disc.setAttribute("aria-label", T.lineDiscount);
      disc.addEventListener("input", function () {
        line.discount = disc.value; line.autoDiscount = false;  // the cashier's own discount replaces the offer
        updateTotals(); q("[data-line-total='" + i + "']").firstChild.textContent = money(lineTotal(line));
      });
      discCell.appendChild(disc);
      tr.appendChild(discCell);

      var total = el("td", "text-end fw-semibold text-nowrap", money(lineTotal(line)));
      total.dataset.lineTotal = i;
      var unitPriceNote = el("small", "d-block text-body-secondary fw-normal", "@ " + money(unitPrice(line) * unitOf(line).factor));
      total.appendChild(unitPriceNote);
      tr.appendChild(total);

      var removeCell = el("td");
      var remove = el("button", "btn btn-sm btn-link text-danger p-0"); remove.type = "button"; remove.setAttribute("aria-label", T.remove);
      remove.innerHTML = '<i class="bi bi-x-lg"></i>';
      remove.addEventListener("click", function () { state.lines.splice(i, 1); render(); });
      removeCell.appendChild(remove);
      tr.appendChild(removeCell);

      cartBody.appendChild(tr);
    });
    updateTotals();
  }

  function updateTotals() {
    var t = totals();
    q("[data-t=subtotal]").textContent = money(t.subtotal);
    q("[data-t=lineDiscount]").textContent = t.lineDiscount ? "− " + money(t.lineDiscount) : money(0);
    q("[data-t=vat]").textContent = money(t.vat);
    q("[data-t=rounding]").textContent = (t.rounding > 0 ? "+ " : "− ") + money(Math.abs(t.rounding));
    // Shows where the rounding comes from, e.g. "৳94.30 rounded to ৳94.00" (Business settings → round to whole taka).
    q("[data-rounding-note]").textContent = fmt(T.roundedFrom, { exact: money(round2(t.total - t.rounding)), total: money(t.total) });
    q("[data-t=total]").textContent = money(t.total);
    document.querySelectorAll("[data-vat-row]").forEach(function (e) { e.classList.toggle("d-none", !num(cfg.vatPercent)); });
    document.querySelectorAll("[data-rounding-row]").forEach(function (e) { e.classList.toggle("d-none", !t.rounding); });
    q("[data-pay]").disabled = state.lines.length === 0;
    var showPoints = t.mostPoints > 0 || num(state.points) > 0;
    document.querySelectorAll("[data-points-row]").forEach(function (e) { e.hidden = !showPoints; });
    if (showPoints && state.customer) {
      q("[data-points-note]").textContent = " " + fmt(T.pointsNote, { n: state.customer.points, most: t.mostPoints });
      var asked = Math.floor(num(state.points));
      q("[data-points-value]").textContent = t.points ? "− " + money(t.pointsValue)
        : (asked > 0 && asked < cfg.loyalty.minRedeem ? fmt(T.pointsMin, { n: cfg.loyalty.minRedeem }) : "");
    }
  }

  var pointsInput = q("[data-points]");
  pointsInput.addEventListener("input", function () { state.points = pointsInput.value; updateTotals(); });
  q("[data-points-max]").addEventListener("click", function () {
    var most = totals().mostPoints;
    state.points = most ? String(most) : ""; pointsInput.value = state.points; updateTotals();
  });

  var discountInput = q("[data-discount]"), discountMode = q("[data-discount-mode]");
  discountInput.addEventListener("input", function () { state.discount = discountInput.value; updateTotals(); });
  discountMode.addEventListener("change", function () { state.discountMode = discountMode.value; updateTotals(); });
  document.querySelectorAll("[name=price_type]").forEach(function (r) {
    r.addEventListener("change", function () { state.priceType = r.value; render(); renderResults(search.value.trim()); });
  });

  // ---- batch choice -------------------------------------------------------------
  function chooseBatch(line) {
    var list = q("[data-batches]");
    list.replaceChildren();
    var auto = el("button", "list-group-item list-group-item-action" + (line.batch ? "" : " active"), T.auto); auto.type = "button";
    auto.addEventListener("click", function () { line.batch = null; line.batchLabel = ""; modal("batchModal").hide(); render(); });
    list.appendChild(auto);
    api(cfg.urls.batches + "?medicine=" + line.medicine).then(function (data) {
      data.results.forEach(function (b) {
        var label = b.batch_no + (b.expiry ? " · " + T.expires + " " + b.expiry.slice(0, 7) : "");
        var item = el("button", "list-group-item list-group-item-action d-flex justify-content-between" + (line.batch === b.id ? " active" : ""));
        item.type = "button";
        item.appendChild(el("span", "", label));
        item.appendChild(el("span", b.expired ? "badge text-bg-danger" : "badge text-bg-light", b.expired ? T.expired : String(b.quantity)));
        item.disabled = b.expired;
        item.addEventListener("click", function () { line.batch = b.id; line.batchLabel = label; modal("batchModal").hide(); render(); });
        list.appendChild(item);
      });
    }).catch(function () { list.appendChild(el("p", "small text-body-secondary m-2 mb-0", T.offlineBatch)); });
    modal("batchModal").show();
  }

  // ---- customer -----------------------------------------------------------------
  var $customer = $("#pos-customer");
  $customer.select2({
    theme: "bootstrap-5", width: "100%", allowClear: true, placeholder: T.walkIn,
    ajax: {
      url: cfg.urls.customers, dataType: "json", delay: 250, data: function (p) { return { q: p.term || "", page: p.page || 1 }; },
      transport: function (params, success, failure) {
        var request = $.ajax(params);
        request.then(success);
        request.fail(function (xhr) {
          if (xhr.status === 0 && offline && offline.hasCopy()) { setOnline(false); success({ results: offline.customers(params.data.q), pagination: { more: false } }); }
          else failure();
        });
        return request;
      }
    }
  });
  $customer.on("select2:select", function (ev) { setCustomer(ev.params.data); });
  $customer.on("select2:clear", function () { setCustomer(null); });

  function setCustomer(c) {
    state.customer = c;
    state.points = ""; pointsInput.value = "";  // points belong to the customer
    var info = q("[data-customer-info]");
    if (!c) { info.textContent = ""; updateTotals(); return; }
    var limit = c.credit_limit == null ? T.noLimit : (num(c.credit_limit) === 0 ? T.noCredit : T.limit + " " + money(c.credit_limit));
    var parts = [T.owes + " " + money(c.balance), limit];
    if (cfg.loyalty && cfg.loyalty.enabled && num(c.points)) parts.push(T.points + " " + c.points);
    if (num(c.default_discount)) {
      parts.push(T.discountPct + " " + num(c.default_discount) + "%");
      if (!num(state.discount)) { state.discountMode = "percent"; state.discount = c.default_discount; discountMode.value = "percent"; discountInput.value = num(c.default_discount); }
    }
    info.textContent = parts.join(" · ");
    updateTotals();
  }

  var customerForm = q("[data-customer-form]");
  var newCustomerBtn = q("[data-new-customer]");
  if (newCustomerBtn) newCustomerBtn.addEventListener("click", function () { customerForm.reset(); q("[data-customer-error]").classList.add("d-none"); modal("customerModal").show(); });
  customerForm.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var data = Object.fromEntries(new FormData(customerForm).entries());
    api(cfg.urls.newCustomer, data).then(function (c) {
      $customer.append(new Option(c.text, c.id, true, true)).trigger("change");
      setCustomer(c);
      modal("customerModal").hide();
    }).catch(function (err) { var box = q("[data-customer-error]"); box.textContent = err.message; box.classList.remove("d-none"); });
  });

  // ---- pay ----------------------------------------------------------------------
  var paymentsBox = q("[data-payments]"), payments = [];
  // The payment the pay box starts with: the shop's choice (Invoice & printing settings), else cash.
  var cashAccount = cfg.accounts.find(function (a) { return a.id === cfg.defaultAccount; })
    || cfg.accounts.find(function (a) { return a.kind === "cash"; }) || cfg.accounts[0];

  function renderPayments() {
    paymentsBox.replaceChildren();
    payments.forEach(function (p, i) {
      var row = el("div", "row g-2 mb-2 align-items-center");
      var acc = el("select", "form-select"); acc.setAttribute("aria-label", T.account);
      cfg.accounts.forEach(function (a) { var o = el("option", "", a.name); o.value = a.id; o.selected = a.id === p.account; acc.appendChild(o); });
      acc.addEventListener("change", function () { p.account = parseInt(acc.value, 10); updatePay(); });
      var amount = el("input", "form-control"); amount.type = "number"; amount.min = 0; amount.step = "0.01"; amount.value = p.amount;
      amount.setAttribute("aria-label", T.amount);
      amount.addEventListener("input", function () { p.amount = amount.value; updatePay(); });
      var c1 = el("div", "col-6"), c2 = el("div", "col-5"), c3 = el("div", "col-1 text-end");
      c1.appendChild(acc); c2.appendChild(amount);
      if (payments.length > 1) {
        var x = el("button", "btn btn-sm btn-link text-danger p-0"); x.type = "button"; x.innerHTML = '<i class="bi bi-x-lg"></i>';
        x.addEventListener("click", function () { payments.splice(i, 1); renderPayments(); });
        c3.appendChild(x);
      }
      row.append(c1, c2, c3);
      paymentsBox.appendChild(row);
    });
    updatePay();
  }

  function updatePay() {
    var t = totals(), paid = payments.reduce(function (s, p) { return s + num(p.amount); }, 0);
    var cashPaid = payments.reduce(function (s, p) { var a = cfg.accounts.find(function (x) { return x.id === p.account; }); return a && a.kind === "cash" ? s + num(p.amount) : s; }, 0);
    var tendered = num(q("[data-tendered]").value);
    q("[data-change]").textContent = tendered > cashPaid && cashPaid > 0 ? money(round2(tendered - cashPaid)) : "—";
    var due = round2(t.total - paid), note = q("[data-due-note]");
    if (due > 0) {
      note.textContent = state.customer ? fmt(T.dueNote, { due: money(due) }) : fmt(T.dueNeedsCustomer, { due: money(due) });
      note.classList.remove("d-none");
    } else { note.classList.add("d-none"); }
  }

  q("[data-add-payment]").addEventListener("click", function () {
    var t = totals(), paid = payments.reduce(function (s, p) { return s + num(p.amount); }, 0);
    var other = cfg.accounts.find(function (a) { return !payments.some(function (p) { return p.account === a.id; }); }) || cfg.accounts[0];
    payments.push({ account: other.id, amount: Math.max(0, round2(t.total - paid)) || "" });
    renderPayments();
  });
  q("[data-tendered]").addEventListener("input", updatePay);

  function openPay() {
    if (!state.lines.length) return;
    var t = totals();
    q("[data-pay-total]").textContent = money(t.total);
    payments = [{ account: cashAccount.id, amount: t.total }];
    q("[data-tendered]").value = "";
    q("[data-rx]").checked = false;
    q("[data-rx-row]").classList.toggle("d-none", !state.lines.some(function (l) { return l.rx; }));
    q("[data-pay-error]").classList.add("d-none");
    renderPayments();
    modal("payModal").show();
  }
  q("[data-pay]").addEventListener("click", openPay);
  document.getElementById("payModal").addEventListener("shown.bs.modal", function () { var i = paymentsBox.querySelector("input"); if (i) i.select(); });

  var completing = false, lastReceipt = null, lastOffline = null;
  q("[data-complete]").addEventListener("click", function () {
    if (completing) return;
    completing = true;
    var t = totals();
    var payload = {
      lines: cartLines(),
      quotation: state.quotationId,
      payments: payments.filter(function (p) { return num(p.amount) > 0; }).map(function (p) { return { account: p.account, amount: num(p.amount) }; }),
      customer: state.customer ? state.customer.id : null,
      priceType: state.priceType,
      discount: t.discount,
      points: t.points,
      cashTendered: q("[data-tendered]").value || null,
      rxChecked: q("[data-rx]").checked
    };
    api(cfg.urls.checkout, payload).then(function (sale) {
      modal("payModal").hide();
      lastReceipt = sale.receiptUrl; lastOffline = null;
      q("[data-done-offline]").classList.add("d-none");
      q("[data-done-number]").textContent = sale.number;
      var change = q("[data-done-change]"), due = q("[data-done-due]");
      change.textContent = sale.change ? T.change + ": " + sale.change : ""; change.classList.toggle("d-none", !sale.change);
      due.textContent = sale.due ? T.due + ": " + sale.due : ""; due.classList.toggle("d-none", !sale.due);
      var points = q("[data-done-points]"), sent = q("[data-done-sent]"), whatsapp = q("[data-done-whatsapp]");
      points.textContent = sale.points ? fmt(T.pointsDone, sale.points) : ""; points.classList.toggle("d-none", !sale.points);
      sent.textContent = sale.sent && sale.sent.length ? T.sent + " " + sale.sent.join(", ") : ""; sent.classList.toggle("d-none", !sent.textContent);
      whatsapp.href = sale.whatsappUrl || "#"; whatsapp.classList.toggle("d-none", !sale.whatsappUrl);
      modal("doneModal").show();
      if (cfg.autoPrint) printQuietly(sale.receiptUrl);
      reset();
    }).catch(function (err) {
      if (err.offline && offline) return saveOffline(payload, t);
      var box = q("[data-pay-error]"); box.textContent = err.message; box.classList.remove("d-none");
    }).finally(function () { completing = false; });
  });

  /* No internet: keep the sale on this computer, print a receipt with a temporary number, send it later. */
  function saveOffline(payload, t) {
    var paid = payload.payments.reduce(function (sum, p) { return sum + num(p.amount); }, 0);
    var tendered = num(payload.cashTendered), change = tendered > paid ? round2(tendered - paid) : 0, due = round2(t.total - paid);
    var receipt = {
      customer: state.customer ? state.customer.name : "",
      lines: state.lines.map(function (l) {
        return { medicine: l.medicine, name: l.name, unit: unitOf(l).label, qty: l.qty, units: unitOf(l).factor * l.qty,
                 price: money(gross(l) / l.qty), total: money(lineTotal(l)) };
      }),
      totals: [{ label: T.subtotal, value: money(t.subtotal) }]
        .concat(t.lineDiscount + t.discount + t.pointsValue ? [{ label: T.lineDiscount, value: "− " + money(t.lineDiscount + t.discount + t.pointsValue) }] : [])
        .concat(t.vat ? [{ label: T.vat, value: money(t.vat) }] : [])
        .concat([{ label: T.total, value: money(t.total), grand: true }, { label: T.paid, value: money(paid) }])
        .concat(change ? [{ label: T.change, value: money(change) }] : [])
        .concat(due > 0 ? [{ label: T.due, value: money(due) }] : [])
    };
    return offline.save(payload, receipt).then(function (row) {
      modal("payModal").hide();
      lastReceipt = null; lastOffline = row;
      q("[data-done-number]").textContent = row.number;
      var changeBox = q("[data-done-change]"), dueBox = q("[data-done-due]");
      changeBox.textContent = change ? T.change + ": " + money(change) : ""; changeBox.classList.toggle("d-none", !change);
      dueBox.textContent = due > 0 ? T.due + ": " + money(due) : ""; dueBox.classList.toggle("d-none", !(due > 0));
      ["[data-done-points]", "[data-done-sent]", "[data-done-whatsapp]"].forEach(function (sel) { q(sel).classList.add("d-none"); });
      q("[data-done-offline]").classList.remove("d-none");
      modal("doneModal").show();
      if (cfg.autoPrint) offline.printReceipt(row, T);
      reset();
    }).catch(function () {
      var box = q("[data-pay-error]"); box.textContent = T.offlineSaveFailed; box.classList.remove("d-none");
    });
  }

  // Prints from a hidden frame on this page, so no pop-up window (or pop-up blocker) is involved.
  function printQuietly(url) {
    var frame = document.getElementById("print-frame");
    if (!frame) {
      frame = document.createElement("iframe");
      frame.id = "print-frame"; frame.className = "print-frame"; frame.setAttribute("aria-hidden", "true"); frame.tabIndex = -1;
      document.body.appendChild(frame);
    }
    frame.src = url + "?print=1&size=" + cfg.receiptSize;
  }

  q("[data-print]").addEventListener("click", function () {
    if (lastReceipt) window.open(lastReceipt + "?print=1&size=" + cfg.receiptSize, "_blank", "width=420,height=640");
    else if (lastOffline) offline.printReceipt(lastOffline, T);
  });
  q("[data-new-sale]").addEventListener("click", function () { modal("doneModal").hide(); });
  document.getElementById("doneModal").addEventListener("hidden.bs.modal", function () { search.focus(); });

  function reset() {
    state.lines = []; state.discount = ""; state.discountMode = "amount"; state.quotationId = null; state.points = "";
    discountInput.value = ""; discountMode.value = "amount"; pointsInput.value = "";
    $customer.val(null).trigger("change"); setCustomer(null);
    search.value = "";
    showBrowse();  // also refreshes the stock numbers on the cards
    render();
  }

  // ---- hold / recall -------------------------------------------------------------
  function hold() {
    if (!state.lines.length) { toast(T.cartEmpty); return; }
    var label = state.customer ? state.customer.name : "";
    api(cfg.urls.hold, { label: label, cart: JSON.parse(JSON.stringify(state)) }).then(function () { toast(T.held); reset(); })
      .catch(function (err) { toast(err.message); });
  }

  function loadCart(cart) {
    state.lines = cart.lines || []; state.priceType = cart.priceType || "retail";
    state.discount = cart.discount || ""; state.discountMode = cart.discountMode || "amount";
    state.quotationId = cart.quotationId || null;
    discountInput.value = state.discount; discountMode.value = state.discountMode;
    document.querySelector("[name=price_type][value='" + state.priceType + "']").checked = true;
    if (cart.customer) { $customer.append(new Option(cart.customer.text, cart.customer.id, true, true)).trigger("change"); setCustomer(cart.customer); }
    else { $customer.val(null).trigger("change"); setCustomer(null); }
    render();
  }

  // ---- quotations ----------------------------------------------------------------
  var quoteBtn = q("[data-quote]");
  if (quoteBtn) {
    quoteBtn.addEventListener("click", function () {
      if (!state.lines.length) { toast(T.cartEmpty); return; }
      q("[data-quote-error]").classList.add("d-none");
      modal("quoteModal").show();
    });
    q("[data-quote-form]").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var t = totals();
      // Open the print window now, while it's still a click: browsers block windows opened after a network wait.
      var printWindow = window.open("", "_blank");
      api(cfg.urls.quote, {
        lines: cartLines(), customer: state.customer ? state.customer.id : null, priceType: state.priceType, discount: t.discount,
        validDays: q("#quote-days").value, note: q("#quote-note").value
      }).then(function (data) {
        modal("quoteModal").hide();
        toast(fmt(T.quoteSaved, { number: data.number }));
        if (printWindow) printWindow.location = data.printUrl + "?print=1";
        q("#quote-note").value = "";
        reset();
      }).catch(function (err) {
        if (printWindow) printWindow.close();
        var box = q("[data-quote-error]"); box.textContent = err.message; box.classList.remove("d-none");
      });
    });
  }

  function showHeld() {
    var list = q("[data-held-list]");
    list.replaceChildren();
    api(cfg.urls.held).then(function (data) {
      if (!data.results.length) { list.appendChild(el("p", "text-body-secondary mb-0", T.noHeld)); return; }
      data.results.forEach(function (h) {
        var item = el("button", "list-group-item list-group-item-action d-flex justify-content-between align-items-center"); item.type = "button";
        var left = el("span");
        left.appendChild(el("span", "fw-semibold d-block", h.label));
        left.appendChild(el("small", "text-body-secondary", h.at + " · " + h.by + " · " + h.items + " " + T.items));
        item.appendChild(left);
        item.appendChild(el("i", "bi bi-arrow-return-left"));
        item.addEventListener("click", function () {
          api(cfg.urls.recall, { id: h.id }).then(function (data) {
            loadCart(data.cart);
            modal("heldModal").hide();
          }).catch(function (err) { toast(err.message); });
        });
        list.appendChild(item);
      });
    });
    modal("heldModal").show();
  }

  q("[data-hold]").addEventListener("click", hold);
  q("[data-held]").addEventListener("click", showHeld);
  q("[data-clear]").addEventListener("click", function () { if (state.lines.length && window.confirm(T.clearConfirm)) reset(); });

  // ---- keyboard -----------------------------------------------------------------
  document.addEventListener("keydown", function (ev) {
    var keys = { F2: function () { document.querySelectorAll(".modal.show").forEach(function (m) { bootstrap.Modal.getInstance(m).hide(); }); search.focus(); search.select(); },
                 F4: openPay, F8: hold, F9: showHeld };
    if (keys[ev.key]) { ev.preventDefault(); keys[ev.key](); }
  });

  render();
  showBrowse();
  if (cfg.quotation) {  // opened from a quotation's "Sell in POS"
    loadCart(Object.assign({}, cfg.quotation.cart, { quotationId: cfg.quotation.id }));
    toast(fmt(T.fromQuote, { number: cfg.quotation.number }));
  }
  // ---- offline sales: the badge and the list -------------------------------------------
  function renderQueue(rows) {
    var badge = q("[data-net]"), count = q("[data-net-count]"), list = q("[data-offline-list]");
    if (!badge) return;
    var failed = rows.filter(function (r) { return r.status === "failed"; }).length;
    count.textContent = rows.length; count.hidden = !rows.length;
    badge.classList.toggle("has-failed", failed > 0);
    list.replaceChildren();
    if (!rows.length) { list.appendChild(el("p", "text-body-secondary mb-0", T.offlineNone)); return; }
    rows.forEach(function (row) {
      var item = el("div", "offline-row" + (row.status === "failed" ? " is-failed" : ""));
      var info = el("div");
      info.appendChild(el("strong", "", row.number));
      info.appendChild(el("small", "d-block text-body-secondary", new Date(row.soldAt).toLocaleString() + " · " +
        row.receipt.totals.filter(function (t) { return t.grand; }).map(function (t) { return t.value; }).join("")));
      if (row.error) info.appendChild(el("small", "d-block text-danger", row.error));
      item.appendChild(info);
      var actions = el("div", "d-flex gap-1");
      if (row.status === "failed") {
        var again = el("button", "btn btn-sm btn-outline-primary", T.offlineRetry); again.type = "button";
        again.addEventListener("click", function () { offline.retry(row.offlineId); });
        var load = el("button", "btn btn-sm btn-outline-secondary", T.offlineLoad); load.type = "button";
        load.addEventListener("click", function () {
          offline.take(row.offlineId).then(function (taken) { if (taken) { modal("offlineModal").hide(); loadOfflineCart(taken); } });
        });
        var drop = el("button", "btn btn-sm btn-link text-danger", T.remove); drop.type = "button";
        drop.addEventListener("click", function () { if (window.confirm(T.offlineDeleteConfirm)) offline.remove(row.offlineId); });
        actions.append(again, load, drop);
      } else {
        actions.appendChild(el("span", "badge text-bg-light", T.offlineWaiting));
      }
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  /* A sale the server refused goes back into the cart, to be fixed and completed again while online. */
  function loadOfflineCart(row) {
    state.lines = [];
    row.lines.forEach(function (l) {
      var m = offline.medicine(l.medicine);
      if (!m) return;
      state.lines.push({ medicine: m.id, name: m.name, rx: m.rx, price: m.price, wholesale: m.wholesale, units: m.units, stock: m.stock,
                         unit: l.unit, qty: l.qty, discount: l.discount ? String(l.discount) : "", batch: null, batchLabel: "", promo: m.promo || null });
    });
    render();
    toast(T.offlineLoaded);
  }

  if (offline) {
    offline.onChange(renderQueue);
    offline.start(Object.assign({}, cfg.offline, { urls: cfg.urls, csrf: function () { return csrf; } }));
    var syncButton = q("[data-offline-sync]");
    if (syncButton) syncButton.addEventListener("click", function () {
      offline.sync().then(function (res) {
        if (res.error === "signin") toast(T.offlineSignIn);
        else if (res.error) toast(T.offline);
        else if (res.sent) toast(fmt(T.offlineSent, { n: res.sent }));
      });
    });
    window.addEventListener("online", function () { setOnline(true); });
    window.addEventListener("offline", function () { setOnline(false); });
    setOnline(navigator.onLine);
  }
})();
