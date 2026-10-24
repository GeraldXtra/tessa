import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

import { defineConfig } from 'electron-vite';
import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';

import { PRODUCTION_CSP } from './src/shared/csp.ts';

function sourceStamp(): string {
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else files.push(p);
    }
  };
  walk(resolve(__dirname, 'src'));
  walk(resolve(__dirname, '../../packages/protocol/src'));
  walk(resolve(__dirname, '../../packages/tokens/dist'));
  files.push(resolve(__dirname, '../../packages/tokens/tokens.json'), resolve(__dirname, 'electron.vite.config.ts'), resolve(__dirname, 'package.json'));
  const hash = createHash('sha256');
  for (const f of files.sort()) {
    hash.update(relative(__dirname, f).replace(/\\/g, '/'));
    hash.update('\0');
    hash.update(readFileSync(f));
    hash.update('\0');
  }
  return hash.digest('hex').slice(0, 16);
}

const SOURCE_STAMP = sourceStamp();

function stampPlugin(part: 'main' | 'preload' | 'renderer'): Plugin {
  return {
    name: 'tessa-build-stamp',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'stamp.json',
        source: JSON.stringify({
          source: SOURCE_STAMP,
          part,
          builtAt: new Date().toISOString(),
          command: process.env['NODE_ENV_ELECTRON_VITE'] === 'development' ? 'npm run dev' : 'npm run build',
        }),
      });
    },
  };
}

/**
 * Injects the production Content-Security-Policy as a <meta> tag.
 *
 * `apply: 'build'` is the whole trick. The production renderer loads from
 * file://, which has no response headers, so a meta tag is the ONLY way to
 * enforce a policy there. But that same tag would also apply under the dev
 * server, where Vite's HMR needs an inline bootstrap script and a websocket —
 * so in dev the placeholder is left as an inert HTML comment and main/index.ts
 * applies the looser development policy over HTTP instead.
 */
function cspMetaPlugin(): Plugin {
  return {
    name: 'tessa-csp-meta',
    apply: 'build',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        return html.replace(
          '<!--%CSP%-->',
          `<meta http-equiv="Content-Security-Policy" content="${PRODUCTION_CSP}" />`,
        );
      },
    },
  };
}

/**
 * electron-vite builds three separate bundles from one config: main, preload,
 * renderer. Directory defaults are used deliberately (src/main/index.ts,
 * src/preload/index.ts, src/renderer/index.html) so there is less config to
 * drift from reality.
 *
 * `build.externalizeDeps` leaves everything listed in package.json
 * `dependencies` as a runtime require and bundles everything else. That split is
 * load-bearing here — see the $comment block in package.json. In short:
 *
 *   dependencies      ws            → externalised (has optional native deps)
 *   devDependencies   @tessa/*       → bundled (their entry point is raw .ts)
 *
 * It defaults to true for main and preload, so stating it is redundant — and
 * stated anyway, on purpose. This is the setting that decides whether
 * @tessa/protocol gets bundled or turns into a runtime `require()` of a .ts file
 * that crashes main on launch. A silent default is exactly how that trap gets
 * reintroduced by someone reorganising package.json.
 *
 * It does not exist for the renderer at all (RendererBuildOptions has no such
 * option): react, react-dom and three must be bundled into the browser context,
 * which has no Node resolver.
 *
 * NOTE the API. electron-vite 5 deprecated the `externalizeDepsPlugin()` helper
 * in favour of this config option. The helper is still exported and still works,
 * so a stale call site fails silently rather than loudly.
 */
export default defineConfig({
  main: {
    plugins: [stampPlugin('main')],
    build: {
      externalizeDeps: true,
      // Readable stack traces from the process that owns the token and the
      // socket are worth more than a few kilobytes.
      minify: false,
      sourcemap: true,
    },
  },

  preload: {
    plugins: [stampPlugin('preload')],
    build: {
      externalizeDeps: true,
      minify: false,
      sourcemap: true,
      rollupOptions: {
        output: {
          // The window runs with sandbox: true, and a sandboxed preload must be
          // CommonJS — Electron cannot load an ES module there. package.json
          // omits "type": "module" for the same reason; this pins the format so
          // it can never be silently reinterpreted.
          format: 'cjs',
          entryFileNames: '[name].js',
        },
      },
    },
  },

  renderer: {
    plugins: [react(), cspMetaPlugin(), stampPlugin('renderer')],
    build: {
      sourcemap: true,

      /**
       * TWO HTML ENTRIES, ONE BUNDLE GRAPH.
       *
       * `index.html` is the full Orb; `widget.html` is the ambient corner
       * sphere. Naming both as inputs is what makes electron-vite emit two
       * pages instead of silently building only the default one — the widget
       * window would then load a file that does not exist in a packaged build
       * and show nothing, which is indistinguishable from the transparency
       * being wrong.
       *
       * Rollup shares what they share: `sphere-engine.ts` and `three` are
       * imported by both and are emitted once, so the widget costs the disc it
       * draws rather than a second copy of the renderer.
       */
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/renderer/index.html'),
          widget: resolve(__dirname, 'src/renderer/widget.html'),
        },
      },

      // Set EXPLICITLY. electron-vite does not minify the renderer by default,
      // and the first build proved it: 1,609 kB across 38,471 lines of readable
      // source. Three.js dominates that, and on an i5-7200U every one of those
      // lines is parse work at startup. Measured effect below.
      minify: 'esbuild',

      // No manualChunks. An earlier revision split three into its own chunk to
      // "keep the app shell parseable first". Measured, the total was byte for
      // byte identical (1,647 kB either way) — it only moved code between two
      // files that both load immediately anyway. Rollup's own tree-shaking is
      // doing the real work: three ships 2,044 kB of ESM and only what the
      // sphere reaches survives.
    },
  },
});
