import { spawn } from 'node:child_process';
import { join } from 'node:path';

export interface GrantResult {
  ok: boolean;
  lastError: number | null;
  exitCode: number | null;
  ms: number;
  detail: string;
}

export const GRANT_TIMEOUT_MS = 4000;

export function validPid(pid: unknown): pid is number {
  return typeof pid === 'number' && Number.isSafeInteger(pid) && pid > 0 && pid <= 0xffffffff;
}

export function foregroundScript(pid: number): string {
  if (!validPid(pid)) throw new Error('not a process id');
  return [
    "$ErrorActionPreference = 'Stop'",
    `Add-Type -Namespace TessaOrb -Name Foreground -MemberDefinition '[DllImport("user32.dll", SetLastError = true)] static extern bool AllowSetForegroundWindow(uint dwProcessId); public static string Grant(uint pid) { bool ok = AllowSetForegroundWindow(pid); int err = System.Runtime.InteropServices.Marshal.GetLastWin32Error(); return (ok ? "1" : "0") + " " + err; }'`,
    `[Console]::Out.WriteLine('ASFW ' + [TessaOrb.Foreground]::Grant(${pid}))`,
    '',
  ].join('\n');
}

export function powershellPath(env: Readonly<Record<string, string | undefined>> = process.env): string | null {
  const root = env['SystemRoot'] ?? env['SYSTEMROOT'] ?? env['windir'] ?? env['WINDIR'];
  return root ? join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe') : null;
}

export function childEnv(env: Readonly<Record<string, string | undefined>> = process.env): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) if (v !== undefined && k.toUpperCase() !== 'LIB') out[k] = v;
  return out;
}

export function parseGrant(stdout: string): { ok: boolean; lastError: number } | null {
  const m = /^ASFW ([01]) (\d{1,10})\s*$/m.exec(stdout);
  return m ? { ok: m[1] === '1', lastError: Number(m[2]) } : null;
}

export function grantForeground(pid: number | null, timeoutMs: number = GRANT_TIMEOUT_MS): Promise<GrantResult> {
  const started = performance.now();
  const elapsed = (): number => Math.round((performance.now() - started) * 10) / 10;
  return new Promise((resolve) => {
    if (!validPid(pid)) {
      resolve({ ok: false, lastError: null, exitCode: null, ms: elapsed(), detail: 'no daemon process id to grant' });
      return;
    }
    const exe = powershellPath();
    if (!exe) {
      resolve({ ok: false, lastError: null, exitCode: null, ms: elapsed(), detail: 'no SystemRoot, so no PowerShell path' });
      return;
    }
    let settled = false;
    let out = '';
    let err = '';
    const finish = (r: Omit<GrantResult, 'ms'>): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ...r, ms: elapsed() });
    };
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(exe, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', '-'], { env: childEnv(), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    } catch (e) {
      resolve({ ok: false, lastError: null, exitCode: null, ms: elapsed(), detail: `could not start PowerShell: ${String(e)}` });
      return;
    }
    const timer = setTimeout(() => {
      child.kill();
      finish({ ok: false, lastError: null, exitCode: null, detail: `no answer from PowerShell within ${timeoutMs} ms` });
    }, timeoutMs);
    child.stdout?.on('data', (d: Buffer) => {
      if (out.length < 4096) out += d.toString('utf8');
    });
    child.stderr?.on('data', (d: Buffer) => {
      if (err.length < 4096) err += d.toString('utf8');
    });
    child.on('error', (e) => finish({ ok: false, lastError: null, exitCode: null, detail: `PowerShell failed: ${e.message}` }));
    child.on('close', (code) => {
      const parsed = parseGrant(out);
      if (!parsed) {
        finish({ ok: false, lastError: null, exitCode: code, detail: `no ASFW line from PowerShell (exit ${String(code)}${err.trim() ? `, stderr ${err.trim().slice(0, 200)}` : ''})` });
        return;
      }
      finish({
        ok: parsed.ok,
        lastError: parsed.lastError,
        exitCode: code,
        detail: parsed.ok ? 'AllowSetForegroundWindow returned TRUE' : `AllowSetForegroundWindow returned FALSE, GetLastError ${parsed.lastError}`,
      });
    });
    child.stdin?.end(foregroundScript(pid));
  });
}
