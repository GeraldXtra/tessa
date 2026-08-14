/**
 * Drive the UI without the operating system. DEV ONLY.
 *
 * ─── why this exists ───
 * Screen CAPTURE stopped being a dependency when the probe started reading the
 * drawing buffer and captures started coming from `webContents.capturePage()`.
 * Screen INPUT never did, and it has cost this project more hours than any
 * measurement in it:
 *
 *   • a click that missed the expand toggle by 17 px, then missed by 17 px again
 *   • fourteen consecutive failures to take the foreground, on one run
 *   • a helper that found the window by `MainWindowTitle`, which is empty until
 *     first paint — a weakness its own comment warned about, which then cost a
 *     60 s poll and a "no orb" abort while the Orb was up and connected
 *   • a capture that photographed the owner's browser instead of the Orb
 *
 * Every one of those is the same root cause: synthetic input needs the
 * foreground, and the foreground on a machine someone else is using is a coin
 * toss. This removes the OS from the loop entirely.
 *
 * ─── it must exercise the REAL handler ───
 * The tempting shortcut is to set the store directly — `railStore.set('trace')`
 * — and that proves nothing about the button. It would pass with the onClick
 * detached, the element unmounted, or the CSS making it unclickable. So this
 * resolves a real element by selector and calls `HTMLElement.click()`, which
 * dispatches a real bubbling click that reaches React's delegated listener and
 * runs the same `onClick` a mouse would. If the selector matches nothing, that
 * is reported as a failure rather than passing silently.
 *
 * What it does NOT do: move the pointer, hold the foreground, or synthesise
 * keystrokes. It cannot test anything that genuinely depends on those — real
 * focus behaviour, the global chord, hover. Those still need the OS, and this
 * does not pretend otherwise.
 */

/**
 * `click:<selector>`, `wait:<ms>`, `state:<agentState>`, separated by `;`.
 *
 * `state:` is the same mechanism as `--force-state`, made changeable mid-run so
 * every state can be captured in ONE launch. On this machine a launch costs
 * 30–60 s and is the dominant cost of any visual comparison, so four launches
 * to see four states is most of an hour.
 *
 * It sets the store directly, and that is honest here BECAUSE the store is the
 * real input to the sphere: the engine reads `agentStateStore.get()` every
 * frame, so this drives the identical path a daemon event would. It proves
 * nothing about the SOCKET, and nothing here claims otherwise — the
 * arrival-to-drawn measurement deliberately only times states the daemon
 * actually sent, and a `state:` step is invisible to it.
 */
export interface DevStep {
  action: 'click' | 'wait' | 'state';
  arg: string;
}

export function parseDevScript(spec: string): DevStep[] {
  const steps: DevStep[] = [];
  for (const raw of spec.split(';')) {
    const part = raw.trim();
    if (part.length === 0) continue;
    const at = part.indexOf(':');
    if (at < 0) continue;
    const action = part.slice(0, at).trim();
    const arg = part.slice(at + 1).trim();
    if (action === 'click' || action === 'wait' || action === 'state') {
      steps.push({ action, arg });
    }
  }
  return steps;
}

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });

/**
 * Run the script, reporting each step. `report` goes to the process log, so the
 * outcome is readable from outside without a screenshot.
 */
export async function runDevScript(
  steps: readonly DevStep[],
  report: (line: string) => void,
  setState?: (state: string) => boolean,
): Promise<void> {
  for (const [i, step] of steps.entries()) {
    if (step.action === 'state') {
      const ok = setState?.(step.arg) ?? false;
      report(`DEV-DRIVE ${i} state "${step.arg}" ${ok ? 'ok' : 'REJECTED (not an AgentState)'}`);
      continue;
    }

    if (step.action === 'wait') {
      const ms = Number.parseInt(step.arg, 10);
      await sleep(Number.isFinite(ms) && ms > 0 ? Math.min(ms, 30_000) : 0);
      report(`DEV-DRIVE ${i} wait ${step.arg}ms`);
      continue;
    }

    let element: Element | null = null;
    try {
      element = document.querySelector(step.arg);
    } catch {
      report(`DEV-DRIVE ${i} click "${step.arg}" INVALID SELECTOR`);
      continue;
    }
    if (!(element instanceof HTMLElement)) {
      // A miss is a failure, loudly. The whole point is that "the click did
      // nothing" and "the click was never sent" must not look alike — which is
      // exactly the distinction the transcript silence turned on.
      report(`DEV-DRIVE ${i} click "${step.arg}" NO MATCH`);
      continue;
    }
    element.click();
    report(`DEV-DRIVE ${i} click "${step.arg}" ok <${element.tagName.toLowerCase()}>`);
  }
  report('DEV-DRIVE done');
}
