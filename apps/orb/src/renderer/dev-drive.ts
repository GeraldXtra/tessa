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
 * Steps, separated by `;`:
 *
 *   click:<selector>              dispatch a real click
 *   wait:<ms>
 *   state:<agentState>
 *   type:<selector>~<text>        set a controlled field the way a key does
 *   dump:<selector>               read one element back into the process log
 *   respond:<requestId>~<approve|deny>   call the bridge directly, no card
 *
 * `~` rather than `|` as the inner separator purely so these survive being
 * passed through cmd.exe, where `|` is a pipe.
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
export type DevAction = 'click' | 'wait' | 'state' | 'type' | 'dump' | 'respond' | 'key';

export interface DevStep {
  action: DevAction;
  arg: string;
}

const ACTIONS: readonly DevAction[] = ['click', 'wait', 'state', 'type', 'dump', 'respond', 'key'];

/**
 * Steps are `;`-separated, so **no argument may contain a semicolon**. The
 * `type:` payloads used to verify the approval card are chosen accordingly.
 * Splitting on something rarer would only move the problem.
 */
export function parseDevScript(spec: string): DevStep[] {
  const steps: DevStep[] = [];
  for (const raw of spec.split(';')) {
    const part = raw.trim();
    if (part.length === 0) continue;
    const at = part.indexOf(':');
    if (at < 0) continue;
    const action = part.slice(0, at).trim();
    // `type:` and `dump:` arguments are NOT trimmed past the delimiter split —
    // trailing space in a payload is exactly the sort of thing an edit test
    // should be able to reproduce.
    const arg = part.slice(at + 1);
    if ((ACTIONS as readonly string[]).includes(action)) {
      steps.push({ action: action as DevAction, arg: action === 'type' ? arg : arg.trim() });
    }
  }
  return steps;
}

/**
 * Set a controlled input's value the way a HUMAN does, not the way JS does.
 *
 * `element.value = x` does not work on a React-controlled field. React installs
 * its own value tracker on the DOM node and compares against it before
 * dispatching a synthetic change event; assigning through the instance property
 * updates the node and the tracker together, so React sees no change and
 * `onChange` never fires. The field would show the new text and the store would
 * still hold the old one — which, on an approval card, is precisely the class
 * of bug that must not be possible to create accidentally.
 *
 * Going through the prototype's setter updates the node WITHOUT touching the
 * tracker, so the subsequent `input` event looks exactly like a keystroke and
 * runs the real `onChange`. Returns false if the descriptor is missing, rather
 * than silently doing nothing.
 */
function setControlledValue(element: HTMLTextAreaElement | HTMLInputElement, value: string): boolean {
  const proto =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (!setter) return false;
  setter.call(element, value);
  element.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
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

    /**
     * `respond:<requestId>~<approve|deny>` — call the bridge DIRECTLY.
     *
     * This deliberately bypasses the card's own guard, and that is the whole
     * point of it. The renderer marks a request as decided and disables both
     * buttons, so clicking APPROVE twice can only ever prove that the button
     * disabled itself. The guarantee that matters is main's: it deletes the
     * request from its pending map before writing to the socket, so a SECOND
     * message carrying the same id — a replayed IPC frame, a renderer bug, a
     * compromised sandbox — finds nothing and is refused. Only a caller that
     * skips the store can test that.
     */
    if (step.action === 'respond') {
      const bar = step.arg.lastIndexOf('~');
      const requestId = bar < 0 ? step.arg : step.arg.slice(0, bar);
      const decision = bar < 0 ? '' : step.arg.slice(bar + 1).trim();
      if (decision !== 'approve' && decision !== 'deny') {
        report(`DEV-DRIVE ${i} respond "${step.arg}" BAD DECISION (want approve|deny)`);
        continue;
      }
      window.zoey.respondToApproval(requestId, decision);
      report(`DEV-DRIVE ${i} respond ${requestId} ${decision} — sent straight to the bridge`);
      continue;
    }

    /**
     * `key:ctrl+shift+M` — dispatch a real KeyboardEvent at `window`.
     *
     * The keyboard shortcuts are `window.addEventListener('keydown')` handlers,
     * so a dispatched event runs the identical handler a physical key runs.
     * What it does NOT prove is that the OS delivers the chord to this window —
     * that needs the foreground, which on a shared machine is a coin toss and
     * has cost this project more hours than any measurement in it.
     *
     * Both `code` and `key` are populated, deliberately. A handler matching
     * only one of them is the bug this action was added to catch: the theme
     * shortcut was written against `code` alone and was dead for every source
     * of synthetic input, including the `keybd_event` test that found it.
     */
    if (step.action === 'key') {
      const parts = step.arg.split('+').map((p) => p.trim()).filter(Boolean);
      const letter = parts.pop() ?? '';
      if (letter.length !== 1) {
        report(`DEV-DRIVE ${i} key "${step.arg}" BAD KEY (want e.g. ctrl+shift+M)`);
        continue;
      }
      const mods = parts.map((p) => p.toLowerCase());
      const event = new KeyboardEvent('keydown', {
        key: letter,
        code: `Key${letter.toUpperCase()}`,
        ctrlKey: mods.includes('ctrl'),
        shiftKey: mods.includes('shift'),
        altKey: mods.includes('alt'),
        bubbles: true,
        cancelable: true,
      });
      window.dispatchEvent(event);
      report(`DEV-DRIVE ${i} key ${step.arg} dispatched (defaultPrevented=${event.defaultPrevented})`);
      continue;
    }

    if (step.action === 'type') {
      const bar = step.arg.indexOf('~');
      const selector = bar < 0 ? step.arg.trim() : step.arg.slice(0, bar).trim();
      const text = bar < 0 ? '' : step.arg.slice(bar + 1);
      let field: Element | null = null;
      try {
        field = document.querySelector(selector);
      } catch {
        report(`DEV-DRIVE ${i} type "${selector}" INVALID SELECTOR`);
        continue;
      }
      if (!(field instanceof HTMLTextAreaElement) && !(field instanceof HTMLInputElement)) {
        report(`DEV-DRIVE ${i} type "${selector}" NO MATCH (or not a field)`);
        continue;
      }
      const ok = setControlledValue(field, text);
      report(
        `DEV-DRIVE ${i} type "${selector}" ${ok ? 'ok' : 'FAILED (no value setter)'} ` +
          `-> ${field.value.length} chars`,
      );
      continue;
    }

    /**
     * `dump:<selector>` — read one element back into the log.
     *
     * So a claim about what was on screen can be checked against a recorded
     * value rather than against a screenshot someone squinted at. The previous
     * round of performance work was argued from screenshotted numbers that
     * turned out to be stale.
     */
    if (step.action === 'dump') {
      let node: Element | null = null;
      try {
        node = document.querySelector(step.arg);
      } catch {
        report(`DEV-DRIVE ${i} dump "${step.arg}" INVALID SELECTOR`);
        continue;
      }
      if (!node) {
        report(`DEV-DRIVE ${i} dump "${step.arg}" NO MATCH`);
        continue;
      }
      const value =
        node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement
          ? node.value
          : (node.textContent ?? '');
      const disabled =
        node instanceof HTMLButtonElement || node instanceof HTMLTextAreaElement
          ? ` disabled=${node.disabled}`
          : '';
      // JSON-quoted so whitespace, newlines and angle brackets survive the log
      // legibly — the markup test depends on being able to see them exactly.
      report(
        `DEV-DRIVE ${i} dump "${step.arg}" len=${value.length}${disabled} ` +
          `value=${JSON.stringify(value.length > 300 ? `${value.slice(0, 150)}…[${value.length - 300} more]…${value.slice(-150)}` : value)}`,
      );
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
    // `disabled` is reported because a click on a disabled button does nothing
    // at all and leaves no other trace. Without this, "the guard refused it"
    // and "the click never landed" look identical in the log — the same
    // distinction the transcript silence turned on.
    const wasDisabled = element instanceof HTMLButtonElement ? element.disabled : false;
    element.click();
    report(
      `DEV-DRIVE ${i} click "${step.arg}" ok <${element.tagName.toLowerCase()}>` +
        `${element instanceof HTMLButtonElement ? ` disabled=${wasDisabled}` : ''}`,
    );
  }
  report('DEV-DRIVE done');
}
