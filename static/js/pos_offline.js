/* ThingBD MediPOS — selling without internet.
 *
 * While online the till keeps a copy of the outlet's medicines, stock and customers in the browser (IndexedDB).
 * When the server can't be reached, pos.js searches that copy and saves sales here; they are sent to the server
 * (apps/sales/offline.py) as soon as it answers again. Each sale has its own id, so sending one twice records it once.
 */
(function () {
  "use strict";

  var DB_NAME = "medipos-pos", BROWSE_LIMIT = 48, REFRESH_MS = 10 * 60 * 1000, SYNC_MS = 30 * 1000, BATCH = 20;
  var cfg = null, snapshot = null, syncing = false, listeners = [], lastSync = 0;

  // ---- storage --------------------------------------------------------------------------
  var dbPromise = null;
  function db() {
    if (!dbPromise) {
      dbPromise = new Promise(function (resolve, reject) {
        var req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = function () {
          req.result.createObjectStore("kv");
          req.result.createObjectStore("queue", { keyPath: "offlineId" });
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    }
    return dbPromise;
  }
  function run(store, mode, work) {
    return db().then(function (conn) {
      return new Promise(function (resolve, reject) {
        var t = conn.transaction(store, mode), result;
        var req = work(t.objectStore(store));
        if (req) req.onsuccess = function () { result = req.result; };
        t.oncomplete = function () { resolve(result); };
        t.onerror = function () { reject(t.error); };
      });
    });
  }
  function kvGet(key) { return run("kv", "readonly", function (s) { return s.get(key); }); }
  function kvPut(key, value) { return run("kv", "readwrite", function (s) { return s.put(value, key); }); }
  function queueAll() { return run("queue", "readonly", function (s) { return s.getAll(); }).then(function (rows) { return (rows || []).sort(byTime); }); }
  function queuePut(row) { return run("queue", "readwrite", function (s) { return s.put(row); }); }
  function queueDelete(id) { return run("queue", "readwrite", function (s) { return s.delete(id); }); }
  function byTime(a, b) { return a.soldAt < b.soldAt ? -1 : a.soldAt > b.soldAt ? 1 : 0; }

  function snapshotKey() { return "snapshot:" + cfg.tenant + ":" + cfg.outlet; }
  function mine(row) { return row.tenant === cfg.tenant && row.user === cfg.user; }
  function changed() { queueAll().then(function (rows) { listeners.forEach(function (fn) { fn(rows.filter(mine), rows); }); }); }

  // ---- the copy of the outlet -----------------------------------------------------------
  function refresh() {
    return fetch(cfg.urls.offlineData, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        if (data.tenant !== cfg.tenant || data.outlet !== cfg.outlet) return;
        snapshot = data;
        return kvPut(snapshotKey(), data);
      }).catch(function () { /* offline or signed out: keep the copy we have */ });
  }

  function words(text) { return String(text || "").toLowerCase().split(/\s+/).filter(Boolean); }

  function search(term) {
    var list = snapshot ? snapshot.medicines : [];
    var t = String(term || "").trim(), lower = t.toLowerCase();
    var exact = list.filter(function (m) { return (m.barcode && m.barcode === t) || (m.code && m.code.toLowerCase() === lower); });
    if (exact.length) return { exact: true, results: exact.slice(0, 1) };
    var parts = words(t);
    var found = list.filter(function (m) {
      var hay = (m.name + " " + m.generic + " " + m.maker + " " + m.code).toLowerCase();
      return parts.every(function (p) { return hay.indexOf(p) >= 0; });
    }).sort(function (a, b) { return (b.stock - a.stock) || a.name.localeCompare(b.name); });
    return { exact: false, results: found.slice(0, 20) };
  }

  function browse(category) {
    var list = snapshot ? snapshot.medicines.slice() : [];
    var hasSales = list.some(function (m) { return m.sold > 0; });
    if (category === "top") {
      list.sort(function (a, b) { return (b.sold - a.sold) || (b.stock - a.stock) || a.name.localeCompare(b.name); });
      list = list.filter(function (m) { return m.sold > 0 || m.stock > 0; });
    } else {
      if (category.indexOf("form:") === 0) {
        var form = category.slice(5);
        list = list.filter(function (m) { return m.form === form; });
      } else if (/^\d+$/.test(category)) {
        list = list.filter(function (m) { return String(m.category) === category; });
      } else if (category !== "all") {
        list = [];
      }
      list.sort(function (a, b) { return ((b.stock > 0) - (a.stock > 0)) || a.name.localeCompare(b.name); });
    }
    return { exact: false, results: list.slice(0, BROWSE_LIMIT), hasSales: hasSales, more: list.length > BROWSE_LIMIT };
  }

  function customers(term) {
    var parts = words(term), list = snapshot ? snapshot.customers : [];
    return list.filter(function (c) {
      var hay = (c.name + " " + (c.phone || "")).toLowerCase();
      return parts.every(function (p) { return hay.indexOf(p) >= 0; });
    }).slice(0, 30);
  }

  // ---- saving and sending sales --------------------------------------------------------
  function newId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "x" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }
  function shortNumber(when) {
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    var code = Math.random().toString(36).slice(2, 6).toUpperCase();
    return "OFF-" + pad(when.getMonth() + 1) + pad(when.getDate()) + "-" + code;
  }

  /* payload: what pos.js would have sent to checkout; receipt: what the offline receipt shows. */
  function save(payload, receipt) {
    var when = new Date();
    var row = Object.assign({}, payload, {
      offlineId: newId(), number: shortNumber(when), soldAt: when.toISOString(),
      tenant: cfg.tenant, user: cfg.user, outlet: cfg.outlet, register: cfg.register,
      receipt: receipt, status: "waiting", error: ""
    });
    // The copy's stock goes down too, so the next offline sale sees what is left.
    if (snapshot) {
      (receipt.lines || []).forEach(function (line) {
        var m = snapshot.medicines.find(function (x) { return x.id === line.medicine; });
        if (m) m.stock = Math.max(0, m.stock - line.units);
      });
      kvPut(snapshotKey(), snapshot);
    }
    return queuePut(row).then(function () { changed(); return row; });
  }

  function payloadOf(row) {
    var keep = ["offlineId", "soldAt", "outlet", "register", "lines", "payments", "customer", "priceType", "discount", "points",
                "cashTendered", "rxChecked", "note"];
    var out = {};
    keep.forEach(function (k) { out[k] = row[k]; });
    return out;
  }

  function sync() {
    if (syncing || !cfg) return Promise.resolve({ sent: 0 });
    syncing = true;
    var sent = 0;
    return queueAll().then(function (rows) {
      var batch = rows.filter(function (r) { return mine(r) && r.status !== "failed"; }).slice(0, BATCH);
      if (!batch.length) return { sent: 0 };
      return fetch(cfg.urls.sync, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrf(), "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify({ sales: batch.map(payloadOf) })
      }).then(function (r) {
        var type = r.headers.get("Content-Type") || "";
        if (!r.ok || type.indexOf("json") < 0) throw new Error(r.status === 403 || type.indexOf("json") < 0 ? "signin" : "server");
        return r.json();
      }).then(function (data) {
        var byId = {};
        batch.forEach(function (row) { byId[row.offlineId] = row; });
        return Promise.all((data.results || []).map(function (res) {
          var row = byId[res.offlineId];
          if (!row) return null;
          if (res.ok) { sent += 1; return queueDelete(row.offlineId); }
          row.status = "failed"; row.error = res.error || "";
          return queuePut(row);
        }));
      }).then(function () {
        lastSync = Date.now();
        if (sent) refresh();
        return { sent: sent };
      });
    }).catch(function (err) {
      return { sent: sent, error: err.message === "signin" ? "signin" : "offline" };
    }).finally(function () { syncing = false; changed(); });
  }

  function retry(id) {
    return queueAll().then(function (rows) {
      var row = rows.find(function (r) { return r.offlineId === id; });
      if (!row) return;
      row.status = "waiting"; row.error = "";
      return queuePut(row);
    }).then(function () { changed(); return sync(); });
  }

  function remove(id) { return queueDelete(id).then(changed); }

  function take(id) {
    /* Load a sale the server refused back into the cart (pos.js does the loading); it leaves the queue. */
    return queueAll().then(function (rows) {
      var row = rows.find(function (r) { return r.offlineId === id; });
      return row ? queueDelete(id).then(function () { changed(); return row; }) : null;
    });
  }

  // ---- the offline receipt -------------------------------------------------------------
  function esc(text) { return String(text == null ? "" : text).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function receiptHtml(row, T) {
    var r = row.receipt, lines = r.lines.map(function (l) {
      return "<tr><td>" + esc(l.name) + "<br><small>" + esc(l.qty + " " + l.unit) + " × " + esc(l.price) + "</small></td><td class=r>" + esc(l.total) + "</td></tr>";
    }).join("");
    var totals = r.totals.map(function (t) { return "<tr" + (t.grand ? " class=g" : "") + "><td>" + esc(t.label) + "</td><td class=r>" + esc(t.value) + "</td></tr>"; }).join("");
    return "<!doctype html><html><head><meta charset=utf-8><title>" + esc(row.number) + "</title><style>" +
      "body{margin:0;font:11.5px/1.4 system-ui,'Hind Siliguri',sans-serif;color:#111}.p{width:76mm;padding:8px 10px}" +
      "h1{font-size:15px;margin:0;text-align:center}.c{text-align:center}.m{color:#555}table{width:100%;border-collapse:collapse}" +
      "td{padding:2px 0;vertical-align:top}.r{text-align:right}.g td{font-weight:700;border-top:1px solid #111;font-size:13px}" +
      "hr{border:0;border-top:1px dashed #999;margin:6px 0}@page{size:80mm auto;margin:0}</style></head><body><div class=p>" +
      "<h1>" + esc(cfg.shop) + "</h1><div class='c m'>" + esc(cfg.outletName) + "</div><hr>" +
      "<div>" + esc(T.offlineNumber) + ": <b>" + esc(row.number) + "</b></div><div>" + esc(new Date(row.soldAt).toLocaleString()) + "</div>" +
      "<div>" + esc(cfg.cashier) + "</div>" + (r.customer ? "<div>" + esc(r.customer) + "</div>" : "") + "<hr>" +
      "<table>" + lines + "</table><hr><table>" + totals + "</table><hr>" +
      "<p class='c m'>" + esc(T.offlineReceiptNote) + "</p></div><script>window.onload=function(){setTimeout(function(){window.print()},200)}<\/script></body></html>";
  }

  function printReceipt(row, T) {
    var frame = document.getElementById("print-frame");
    if (!frame) {
      frame = document.createElement("iframe");
      frame.id = "print-frame"; frame.className = "print-frame"; frame.setAttribute("aria-hidden", "true"); frame.tabIndex = -1;
      document.body.appendChild(frame);
    }
    frame.removeAttribute("src");
    frame.srcdoc = receiptHtml(row, T);
  }

  // ---- start ---------------------------------------------------------------------------
  function start(config) {
    cfg = config;
    if (!window.indexedDB) return Promise.resolve(false);
    var ready = kvGet(snapshotKey()).then(function (copy) { snapshot = copy || null; }).catch(function () {});
    ready.then(function () { refresh(); sync(); changed(); });
    window.addEventListener("online", function () { sync(); });
    setInterval(function () { refresh(); }, REFRESH_MS);
    setInterval(function () { queueAll().then(function (rows) { if (rows.some(mine)) sync(); }); }, SYNC_MS);
    return ready.then(function () { return true; });
  }

  window.MPOffline = {
    start: start, refresh: refresh, search: search, browse: browse, customers: customers, save: save, sync: sync,
    retry: retry, remove: remove, take: take, printReceipt: printReceipt, queue: queueAll,
    onChange: function (fn) { listeners.push(fn); }, hasCopy: function () { return !!snapshot; },
    medicine: function (id) { return snapshot ? snapshot.medicines.find(function (m) { return m.id === id; }) || null : null; },
    copyTime: function () { return snapshot ? snapshot.at : null; }, isMine: function (row) { return mine(row); }
  };
})();
