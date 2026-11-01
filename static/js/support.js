/* The MediPOS help chat (templates/support/_widget.html). */
(function () {
  var root = document.querySelector("[data-support]");
  if (!root) return;
  var panel = root.querySelector(".support-panel"), toggle = root.querySelector("[data-support-toggle]");
  var log = root.querySelector("[data-support-log]"), form = root.querySelector("[data-support-form]"), input = root.querySelector("[data-support-input]");
  var thread = null, loaded = false, busy = false, welcome = "";

  function csrf() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  // A little formatting from the answers: **bold**, _a note_, [a page](/path) (only links inside MediPOS), line breaks.
  function format(text) {
    var safe = String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    safe = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/(^|\n)_(.+?)_(?=\n|$)/g, "$1<em>$2</em>");
    safe = safe.replace(/\[([^\]]+)\]\((\/[^)\s]*)\)/g, '<a href="$2">$1</a>');
    return safe.replace(/\n/g, "<br>");
  }

  function bubble(role, text, extra) {
    var item = document.createElement("div");
    item.className = "support-msg is-" + role + (extra ? " " + extra : "");
    item.innerHTML = format(text);
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
    return item;
  }

  function show(state) {
    log.replaceChildren();
    welcome = state.welcome || welcome;
    bubble("assistant", welcome);
    (state.messages || []).forEach(function (m) { bubble(m.role, m.content, m.source === "help" ? "is-help" : ""); });
  }

  function load() {
    fetch(root.dataset.stateUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (state) { thread = state.thread; loaded = true; show(state); })
      .catch(function () { bubble("assistant", root.dataset.failed, "is-error"); });
  }

  function open(yes) {
    panel.hidden = !yes;
    toggle.setAttribute("aria-expanded", yes ? "true" : "false");
    root.classList.toggle("is-open", yes);
    if (yes) {
      if (!loaded) load();
      setTimeout(function () { input.focus(); }, 50);
    }
  }

  toggle.addEventListener("click", function () { open(panel.hidden); });
  root.querySelector("[data-support-close]").addEventListener("click", function () { open(false); toggle.focus(); });
  root.querySelector("[data-support-new]").addEventListener("click", function () { thread = null; show({ messages: [] }); input.focus(); });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && !panel.hidden) { open(false); toggle.focus(); } });

  input.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = input.value.trim();
    if (!text || busy) return;
    busy = true;
    bubble("user", text);
    input.value = "";
    var waiting = bubble("assistant", root.dataset.thinking, "is-waiting");
    fetch(root.dataset.sendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ message: text, thread: thread, page: window.location.pathname })
    }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        waiting.remove();
        if (!res.ok) { bubble("assistant", res.data.error || root.dataset.failed, "is-error"); return; }
        thread = res.data.thread;
        bubble("assistant", res.data.answer.content, res.data.answer.source === "help" ? "is-help" : "");
      })
      .catch(function () { waiting.remove(); bubble("assistant", root.dataset.failed, "is-error"); })
      .finally(function () { busy = false; input.focus(); });
  });
})();
