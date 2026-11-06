/**
 * Sends this session's CSRF token with every same-origin request that changes data.
 * Loaded before any other script so every fetch() call is covered.
 */
(function () {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const token = meta ? meta.getAttribute('content') : '';
    const originalFetch = window.fetch.bind(window);
    const SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS'];

    window.fetch = function (input, init) {
        init = init || {};
        const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const url = new URL(input instanceof Request ? input.url : input, window.location.href);

        if (token && !SAFE_METHODS.includes(method) && url.origin === window.location.origin) {
            const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
            headers.set('X-CSRF-Token', token);
            init = Object.assign({}, init, { headers });
        }
        return originalFetch(input, init);
    };
})();
