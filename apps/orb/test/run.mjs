import { spawnSync } from 'node:child_process';
import { mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..', '..', '..');
const out = mkdtempSync(join(tmpdir(), 'orb-tests-'));
const tsc = join(repo, 'node_modules', 'typescript', 'bin', 'tsc');
const built = spawnSync(process.execPath, [tsc, '-p', join(here, 'tsconfig.json'), '--outDir', out], { stdio: 'inherit' });
let status = built.status ?? 1;
if (status === 0) {
  const testDir = join(out, 'apps', 'orb', 'test');
  const files = readdirSync(testDir)
    .filter((f) => f.endsWith('.test.js'))
    .map((f) => join(testDir, f));
  const run = spawnSync(process.execPath, ['--test', '--test-reporter=spec', ...files], {
    stdio: 'inherit',
    env: { ...process.env, NODE_PATH: join(repo, 'node_modules'), TESSA_ORB_DIR: join(here, '..') },
  });
  status = run.status ?? 1;
}
rmSync(out, { recursive: true, force: true });
process.exit(status);
