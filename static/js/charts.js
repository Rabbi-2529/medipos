/* ThingBD MediPOS — small, dependency-free charts.
   MP.columnChart(container, points, opts): one series of daily columns.
   points: [{label, long, total, count}]; opts: {money(v), compact(v), texts: {invoices}}.
   Marks: <=24px columns with a 4px rounded top, square at the baseline; hairline grid; hover/focus tooltip per column. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  // A clean top for the y-axis: 1, 2, 2.5 or 5 times a power of ten, with 4 equal steps.
  function niceMax(max) {
    if (max <= 0) return 4;
    var step = max / 4, pow = Math.pow(10, Math.floor(Math.log10(step))), unit = step / pow;
    var nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
    return nice * pow * 4;
  }

  // Column with a 4px rounded data-end and a square base.
  function columnPath(x, y, w, h) {
    var r = Math.min(4, w / 2, h);
    return "M" + x + "," + (y + h) + "V" + (y + r) + "Q" + x + "," + y + " " + (x + r) + "," + y +
           "H" + (x + w - r) + "Q" + (x + w) + "," + y + " " + (x + w) + "," + (y + r) + "V" + (y + h) + "Z";
  }

  MP.columnChart = function (box, points, opts) {
    var W = 720, H = 240, left = 52, right = 8, top = 14, bottom = 26;
    var plotW = W - left - right, plotH = H - top - bottom;
    var max = niceMax(Math.max.apply(null, points.map(function (p) { return p.total; })));
    var band = plotW / points.length, barW = Math.min(24, band - 2);
    var chart = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "mp-chart", role: "img" });
    chart.setAttribute("aria-label", opts.title || "");

    for (var i = 0; i <= 4; i++) {  // gridlines + y ticks (compact: 0 / 1K / 2K)
      var gy = top + plotH - plotH * i / 4;
      chart.appendChild(svg("line", { x1: left, x2: W - right, y1: gy, y2: gy, class: "mp-chart-grid" }));
      var tick = svg("text", { x: left - 8, y: gy + 4, "text-anchor": "end", class: "mp-chart-tick" });
      tick.textContent = opts.compact(max * i / 4);
      chart.appendChild(tick);
    }

    var tip = document.createElement("div");
    tip.className = "mp-chart-tip";
    tip.hidden = true;
    var tipValue = document.createElement("strong"), tipLabel = document.createElement("span");
    tip.append(tipValue, tipLabel);

    points.forEach(function (p, i) {
      var h = max ? plotH * p.total / max : 0, x = left + band * i + (band - barW) / 2, y = top + plotH - h;
      var g = svg("g", { class: "mp-chart-col", tabindex: "0" });
      g.setAttribute("aria-label", p.long + ": " + opts.money(p.total));
      // The hit target is the whole band, not just the painted column.
      g.appendChild(svg("rect", { x: left + band * i, y: top, width: band, height: plotH, class: "mp-chart-hit" }));
      if (h > 0) g.appendChild(svg("path", { d: columnPath(x, y, barW, Math.max(h, 1)), class: "mp-chart-bar" }));
      if (i % 5 === 4 || i === points.length - 1 || i === 0) {
        var label = svg("text", { x: x + barW / 2, y: H - 8, "text-anchor": "middle", class: "mp-chart-tick" });
        label.textContent = p.label;
        chart.appendChild(label);
      }
      function show() {
        tipValue.textContent = opts.money(p.total);
        tipLabel.textContent = p.long + " · " + p.count + " " + opts.texts.invoices;
        tip.hidden = false;
        var rect = box.getBoundingClientRect(), scale = rect.width / W;
        var px = (x + barW / 2) * scale, py = y * scale;
        tip.style.left = Math.min(Math.max(px, 70), rect.width - 70) + "px";
        tip.style.top = Math.max(py - 8, 0) + "px";
        g.classList.add("is-active");
      }
      function hide() { tip.hidden = true; g.classList.remove("is-active"); }
      g.addEventListener("pointerenter", show);
      g.addEventListener("pointerleave", hide);
      g.addEventListener("focus", show);
      g.addEventListener("blur", hide);
      chart.appendChild(g);
    });
    chart.appendChild(svg("line", { x1: left, x2: W - right, y1: top + plotH, y2: top + plotH, class: "mp-chart-axis" }));
    box.replaceChildren(chart, tip);
  };
})();
