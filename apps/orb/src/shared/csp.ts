/**
 * Content-Security-Policy for the Orb renderer.
 *
 * The one directive that matters most is `connect-src 'none'`.
 *
 * CONTRACT §2.3 says the WebSocket client must live in the main process, not a
 * renderer. That is normally enforced by convention — "we just don't write
 * socket code in the renderer". `connect-src 'none'` makes it mechanical: even
 * if renderer code did call `new WebSocket('ws://127.0.0.1:…')`, or an injected
 * script did, the browser engine refuses to open it. The renderer cannot reach
 * the daemon, cannot exfiltrate anything, and cannot fetch a remote payload.
 *
 * `default-src 'none'` then means every other capability has to be granted
 * explicitly below, so a future directive is added deliberately rather than
 * inherited by accident.
 *
 * This lives in `shared/` because two places need the same string and they must
 * not drift: the build injects it as a <meta> tag (the only enforcement that
 * works for a file:// production renderer, which has no response headers), and
 * main/index.ts also sends it as a header.
 */

const PRODUCTION_DIRECTIVES = [
  "default-src 'none'",
  "script-src 'self'",
  // Vite emits real .css files in production, but React inline `style` props
  // and the sphere's CSS-variable writes still require inline styles. This is
  // the one relaxation, and it cannot load anything remote.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  // The whole point. See the header comment.
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
  "worker-src 'none'",
];

export const PRODUCTION_CSP = PRODUCTION_DIRECTIVES.join('; ');

/**
 * Development policy. Looser, because Vite's HMR client needs an inline
 * bootstrap script and a WebSocket back to the dev server — but deliberately
 * NOT loose enough to let the renderer reach the daemon.
 *
 * `connect-src` is pinned to the dev server's own origin. The daemon lives on a
 * different port, so a renderer-side socket to it is still blocked in dev,
 * which is where such a mistake would actually be written.
 */
export function developmentCsp(devServerOrigin: string): string {
  const wsOrigin = devServerOrigin.replace(/^http/, 'ws');
  return [
    "default-src 'none'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src 'self' ${devServerOrigin} ${wsOrigin}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
  ].join('; ');
}
