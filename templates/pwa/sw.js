{% load static %}/* ThingBD MediPOS service worker (served from /sw.js by apps.core.pwa).
 * The POS page opens from the copy kept here when there's no internet; pos_offline.js does the selling.
 * Other pages need the internet and show a short "you're offline" page instead. Requests that change data,
 * and the POS's own API calls, always go to the server. */
var VERSION = "{{ version }}";
var SHELL = "medipos-shell-" + VERSION, RUNTIME = "medipos-runtime-" + VERSION;
var POS_URL = "{% url 'sales:pos' %}", OFFLINE_URL = "{% url 'core:offline' %}";
// The POS page tells us which files it uses ("keep" message below), so no file list (or version numbers) lives here.
var PRECACHE = [OFFLINE_URL, "{% static 'img/pwa-192.png' %}"];
var CDN_HOSTS = ["cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(SHELL).then(function (cache) { return cache.addAll(PRECACHE); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== SHELL && k !== RUNTIME; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function keep(request, response) {
  if (response && (response.ok || response.type === "opaque")) {
    var copy = response.clone();
    caches.open(RUNTIME).then(function (cache) { cache.put(request, copy); });
  }
  return response;
}

// The POS page, once open, asks for itself and everything it loaded to be kept for days without internet.
self.addEventListener("message", function (event) {
  if (!event.data || event.data.type !== "keep" || !Array.isArray(event.data.urls)) return;
  event.waitUntil(caches.open(RUNTIME).then(function (cache) {
    return Promise.all(event.data.urls.map(function (u) {
      var cross = new URL(u, self.location.href).origin !== self.location.origin;
      return fetch(u, cross ? { mode: "no-cors" } : { credentials: "same-origin" }).then(function (r) {
        if (r.ok || r.type === "opaque") return cache.put(u, r);
      }).catch(function () {});
    }));
  }));
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);

  if (request.mode === "navigate") {
    if (url.pathname === POS_URL) {
      // The latest POS page when online (kept for later); the kept one when not.
      event.respondWith(fetch(request).then(function (response) {
        if (response.ok && !response.redirected) {
          var copy = response.clone();
          caches.open(RUNTIME).then(function (cache) { cache.put(POS_URL, copy); });
        }
        return response;
      }).catch(function () {
        return caches.match(POS_URL).then(function (hit) { return hit || caches.match(OFFLINE_URL); });
      }));
    } else {
      event.respondWith(fetch(request).catch(function () { return caches.match(OFFLINE_URL); }));
    }
    return;
  }

  if (url.origin === self.location.origin && url.pathname.indexOf("{% get_static_prefix %}") === 0) {
    event.respondWith(caches.match(request).then(function (hit) { return hit || fetch(request).then(function (r) { return keep(request, r); }); }));
  } else if (CDN_HOSTS.indexOf(url.hostname) >= 0) {
    // Bootstrap, jQuery, fonts: answer from the copy at once, refresh it in the background.
    event.respondWith(caches.match(request).then(function (hit) {
      var fresh = fetch(request).then(function (r) { return keep(request, r); }).catch(function () { return hit; });
      return hit || fresh;
    }));
  }
});
