/* DELTARUNE web port - asset cache service worker.
 *
 * GitHub Pages sends 10-minute cache headers on the big game files, so every
 * revisit used to re-download ~60-170 MB of chapter data before you could
 * even reach save select. This worker keeps the immutable game files in Cache
 * Storage permanently: reloads boot from disk in ~300ms instead.
 *
 * - Immutable assets (chapter parts, runner engine files, mus/, vid/,
 *   chapter asset dirs, favicon): cache-first, keyed by PATH (the loader's
 *   ?v=&r= cache-busting query is ignored on purpose - these files never
 *   change; see CACHE_VERSION for deploys).
 * - HTML documents: network-first so page updates always land, with a cache
 *   fallback for offline play.
 * - Range requests (video elements, the resumable downloader): served as
 *   proper 206 slices from the cached full body.
 *
 * When a deploy changes any cached file, bump CACHE_VERSION: clients purge
 * the old cache on the next visit and re-download the changed set.
 */
'use strict';

var CACHE_VERSION = 'v9.11';
var CACHE = 'dr-assets-' + CACHE_VERSION;

var IMMUTABLE = [
    /\.unx\.part\d+$/,
    /^\/runner\d*\.(data|js|wasm)$/,
    /^\/audio-worklet\.js$/,
    /^\/favicon\.ico$/,
    /^\/mus\//,
    /^\/vid\//,
    /^\/chapter\d+_windows\//
];

function isImmutablePath(path) {
    for (var i = 0; i < IMMUTABLE.length; i++) {
        if (IMMUTABLE[i].test(path)) return true;
    }
    return false;
}

self.addEventListener('install', function (event) {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', function (event) {
    event.waitUntil((function () {
        return caches.keys().then(function (keys) {
            return Promise.all(keys.map(function (k) {
                if (k !== CACHE) return caches.delete(k);
            }));
        }).then(function () {
            return self.clients.claim();
        });
    })());
});

function cacheKeyFor(request) {
    // key by origin+pathname: the loader's cache-busting query strings are
    // ignored so retries and version bumps all hit the same entry
    var u = new URL(request.url);
    return u.origin + u.pathname;
}

function slice206(buffer, request, contentType) {
    var range = request.headers.get('range');
    var m = range && range.match(/bytes=(\d+)-(\d*)/);
    var total = buffer.byteLength;
    var start = 0, end = total - 1;
    if (m) {
        start = parseInt(m[1], 10);
        if (m[2]) end = Math.min(parseInt(m[2], 10), total - 1);
    }
    if (start > end || start >= total) {
        return new Response(null, {
            status: 416,
            headers: { 'Content-Range': 'bytes */' + total }
        });
    }
    var chunk = buffer.slice(start, end + 1);
    return new Response(chunk, {
        status: 206,
        headers: {
            'Content-Type': contentType || 'application/octet-stream',
            'Content-Range': 'bytes ' + start + '-' + end + '/' + total,
            'Content-Length': String(chunk.byteLength),
            'Accept-Ranges': 'bytes'
        }
    });
}

async function cacheFirst(event) {
    var request = event.request;
    var key = cacheKeyFor(request);
    var cache = await caches.open(CACHE);
    var cached = await cache.match(key);
    if (cached) {
        if (request.headers.has('range')) {
            var buf = await cached.arrayBuffer();
            return slice206(buf, request, cached.headers.get('content-type'));
        }
        return cached;
    }
    // fetch the FULL resource (never cache a partial 206 body)
    var response = await fetch(key, { credentials: 'same-origin' });
    if (!response.ok) return response;
    var clone = response.clone();
    try {
        await cache.put(key, clone);
    } catch (e) {
        /* quota exceeded or cache error: keep going without caching */
    }
    if (request.headers.has('range')) {
        var buf2 = await response.arrayBuffer();
        return slice206(buf2, request, response.headers.get('content-type'));
    }
    return response;
}

async function networkFirst(event) {
    var request = event.request;
    var key = cacheKeyFor(request);
    var cache = await caches.open(CACHE);
    try {
        var fresh = await fetch(request, { credentials: 'same-origin' });
        if (fresh.ok) {
            try { await cache.put(key, fresh.clone()); } catch (e) {}
        }
        return fresh;
    } catch (e) {
        var cached = await cache.match(key);
        if (cached) return cached;
        throw e;
    }
}

self.addEventListener('fetch', function (event) {
    var url = new URL(event.request.url);
    if (url.origin !== location.origin) return;
    if (event.request.method !== 'GET') return;
    var path = url.pathname;
    if (path.endsWith('/sw.js')) return;
    if (isImmutablePath(path)) {
        event.respondWith(cacheFirst(event));
    } else if (path === '/' || path.endsWith('.html')) {
        event.respondWith(networkFirst(event));
    }
    /* everything else: plain network, browser cache applies */
});
