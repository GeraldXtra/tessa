import { resolve } from 'node:path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'

/**
 * electron-vite 5 configuration.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY `build.externalizeDeps` AND NOT `externalizeDepsPlugin`
 * ─────────────────────────────────────────────────────────────────────────────
 * `externalizeDepsPlugin` is the PRE-v5 API. In electron-vite 5 it carries a
 * literal `@deprecated use 'build.externalizeDeps' config option instead` in
 * node_modules/electron-vite/dist/index.d.ts. Most templates and tutorials
 * online still show the plugin.
 *
 * The type is:
 *     build.externalizeDeps?: boolean | { exclude?: string[]; include?: string[] }
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHAT EXTERNALIZATION DECIDES, AND WHY EACH CHOICE IS FORCED
 * ─────────────────────────────────────────────────────────────────────────────
 * electron-vite externalizes `dependencies` and bundles `devDependencies`.
 * "External" means Node `require()`s it at runtime; "bundled" means Rollup
 * inlines it. Getting either backwards is a crash on launch, not a warning.
 *
 *   EXTERNAL (must stay `dependencies`)
 *     @lydell/node-pty — a native addon. It resolves conpty.node through a
 *       runtime dynamic require plus an on-disk package.json read. Bundling it
 *       is fatal. Listed again in rollupOptions.external as belt-and-braces.
 *     ws — bundling it drags in its optional native deps (bufferutil,
 *       utf-8-validate) as unresolvable imports.
 *
 *   BUNDLED (must stay `devDependencies`, and listed in `exclude` below)
 *     @tessa/protocol — its entry is "main": "./src/index.ts". RAW TypeScript,
 *       noEmit, and it imports './enums.generated.ts' *with* the extension.
 *       Externalized, main would require() a .ts file and crash immediately.
 *     @tessa/tokens — same reasoning; consumed as source.
 *
 * `exclude` here is belt-and-braces: both are already devDependencies, so they
 * are bundled by default. Naming them documents the intent so nobody "tidies"
 * them into `dependencies` later and breaks launch.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * NO "type": "module" IN package.json — LOAD-BEARING
 * ─────────────────────────────────────────────────────────────────────────────
 * A sandboxed Electron preload CANNOT be an ES module. Declaring the package
 * ESM makes electron-vite emit ESM for main AND preload, and the preload then
 * fails to load at runtime. Source stays ESM TypeScript; electron-vite emits
 * CJS for main/preload and the renderer stays ESM through Vite.
 */
export default defineConfig({
  main: {
    build: {
      externalizeDeps: {
        exclude: ['@tessa/protocol', '@tessa/tokens'],
      },
      rollupOptions: {
        // The native addon must never be inlined.
        external: ['@lydell/node-pty'],
        input: {
          index: resolve(__dirname, 'src/main/index.ts'),

          // The utilityProcess entry is a SECOND INPUT on the MAIN config, not a
          // new top-level config section, so it inherits main's externalization
          // and @lydell/node-pty stays external. As its own section it would be
          // silently re-bundled, breaking the addon's runtime binary resolution.
          'pty-host': resolve(__dirname, 'src/pty-host/index.ts'),
        },
      },
    },
  },

  preload: {
    build: {
      externalizeDeps: {
        exclude: ['@tessa/protocol'],
      },
    },
  },

  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    plugins: [react()],
    resolve: {
      alias: {
        '@renderer': resolve(__dirname, 'src/renderer'),
      },
    },
    build: {
      rollupOptions: {
        input: resolve(__dirname, 'src/renderer/index.html'),
      },
    },
  },
})
