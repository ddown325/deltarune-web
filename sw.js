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
 *
 * v9.13 - the poisoned-entry fixes ("fails to load, RETRYING DOWNLOAD 5/6"
 * on some devices, only fixable by deleting ALL site data - saves included):
 *
 * 1. Only COMPLETE bodies are cached. The v9 worker cached whatever the CDN
 *    handed it: when GitHub Pages/Fastly closed a big download early (the
 *    same "clean but short" body the page downloader has defended against
 *    since v7.1), the truncated copy was stored PERMANENTLY under the
 *    immutable key. Every later load served that truncated copy, the page's
 *    Range resume then asked for bytes past the end (416), all six retry
 *    rounds hit the same poisoned entry, and the game died at
 *    "RETRYING DOWNLOAD 5/6 -> DOWNLOAD FAILED" forever. Now a fetched body
 *    is buffered and compared against its own Content-Length before it is
 *    allowed into the cache.
 * 2. Self-heal: if a Range request asks for a start byte at/after the end of
 *    a cached body (only possible for a poisoned entry - the page ranges
 *    inside the known file size), the entry is deleted and re-fetched from
 *    the network, verified and re-cached.
 * 3. Storage-blocked fallback: on devices where Cache Storage is unavailable
 *    or throws (Safari private mode, storage-policy-locked browsers, quota
 *    edge states) the old worker REJECTED, which killed every request it
 *    handled - the game could not load at all even though plain network
 *    worked. All caches access is now guarded and falls back to plain fetch.
 */
'use strict';

var CACHE_VERSION = 'v9.13';
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
    event.waitUntil((async function () {
        try {
            var keys = await caches.keys();
            await Promise.all(keys.map(function (k) {
                if (k !== CACHE) return caches.delete(k);
            }));
        } catch (e) { /* storage blocked: keep serving without a cache */ }
        try { return await self.clients.claim(); } catch (e) {}
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

// Fetch the FULL resource (never a partial 206 body) from the network,
// verify its completeness against its own Content-Length header, and only
// then store it. Returns the verified response (plus its buffer so Range
// requests can be sliced without a second read).
async function fetchCompleteAndCache(key, cache) {
    var response = await fetch(key, { credentials: 'same-origin' });
    if (!response.ok) {
        return { response: response, buffer: null };
    }
    var expected = parseInt(response.headers.get('content-length') || '0', 10);
    var buffer = await response.arrayBuffer();
    var complete = expected > 0 ? buffer.byteLength === expected : true;
    if (complete && cache) {
        try {
            await cache.put(key, new Response(buffer, {
                status: 200,
                statusText: 'OK',
                headers: new Headers(response.headers)
            }));
        } catch (e) {
            /* quota exceeded or cache error: keep going without caching */
        }
    }
    return {
        response: new Response(buffer, {
            status: response.status,
            headers: new Headers(response.headers)
        }),
        buffer: buffer
    };
}

async function cacheFirst(event) {
    var request = event.request;
    var key = cacheKeyFor(request);
    var cache = null;
    try { cache = await caches.open(CACHE); } catch (e) { cache = null; }
    if (cache) {
        var cached = null;
        try { cached = await cache.match(key); } catch (e) { cached = null; }
        if (cached) {
            if (!request.headers.has('range')) return cached;
            var buf = await cached.arrayBuffer();
            var m = request.headers.get('range').match(/bytes=(\d+)-/);
            var start = m ? parseInt(m[1], 10) : 0;
            if (start < buf.byteLength) {
                return slice206(buf, request, cached.headers.get('content-type'));
            }
            // v9.13 self-heal: the downloader only ranges inside the known
            // file size, so a start at/after the cached body's end means the
            // entry is truncated (v9-era poison). Drop it; the network path
            // below re-fetches, verifies and re-caches a complete copy.
            try { await cache.delete(key); } catch (e) {}
        }
    }
    var r;
    try {
        r = await fetchCompleteAndCache(key, cache);
    } catch (e) {
        // network error (offline, aborted): nothing new cached; the page's
        // retry/resume logic handles it
        throw e;
    }
    if (request.headers.has('range')) {
        if (r.buffer) {
            var m2 = request.headers.get('range').match(/bytes=(\d+)-/);
            var start2 = m2 ? parseInt(m2[1], 10) : 0;
            return slice206(r.buffer, request, r.response.headers.get('content-type'));
        }
        return r.response; // non-ok status: pass through, the caller retries
    }
    return r.response;
}

async function networkFirst(event) {
    var request = event.request;
    try {
        var fresh = await fetch(request, { credentials: 'same-origin' });
        if (fresh.ok) {
            try {
                var cache = await caches.open(CACHE);
                await cache.put(cacheKeyFor(request), fresh.clone());
            } catch (e) {}
        }
        return fresh;
    } catch (e) {
        try {
            var cache2 = await caches.open(CACHE);
            var cached = await cache2.match(cacheKeyFor(request));
            if (cached) return cached;
        } catch (e2) { /* storage blocked: rethrow the network error */ }
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
