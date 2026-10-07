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
 *   key:<chord>                   keydown at window (the app's own chords)
 *   press:<selector>~<chord>      keydown+keyup ON an element (React handlers)
 *   keys:<selector>~[<ms>~]<text> type character by character, paced
 *   attr:<selector>~<name>        one attribute and the element's rect
 *   count:<selector>              how many elements match
 *   focus:<selector>              focus it, report what has focus
 *   active:                       report what has focus, and whether the window does
 *   capture:<name>                one capturePage() now, into TESSA_CAPTURE_DIR
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
export type DevAction =
  | 'click'
  | 'wait'
  | 'state'
  | 'type'
  | 'dump'
  | 'respond'
  | 'key'
  | 'press'
  | 'keys'
  | 'attr'
  | 'count'
  | 'focus'
  | 'active'
  | 'capture'
  | 'pclick'
  | 'kclick'
  | 'scroll'
  | 'until';

export interface DevStep {
  action: DevAction;
  arg: string;
}

const ACTIONS: readonly DevAction[] = [
  'click',
  'wait',
  'state',
  'type',
  'dump',
  'respond',
  'key',
  'press',
  'keys',
  'attr',
  'count',
  'focus',
  'active',
  'capture',
  'pclick',
  'kclick',
  'scroll',
  'until',
];

/**
 * A key by name, as the compose-box round needed it: `key:` only knew single
 * letters, and Enter, Escape and Space are the three keys the box is about.
 * `code` is the physical key so a handler that matches `code` first (every
 * handler here) sees what a keyboard would send; `key` is populated too, for
 * the handlers that fall back to it.
 */
function keyByName(name: string): { key: string; code: string } | null {
  const n = name.trim();
  if (n.length === 1) {
    if (/[a-z]/i.test(n)) return { key: n, code: `Key${n.toUpperCase()}` };
    if (/[0-9]/.test(n)) return { key: n, code: `Digit${n}` };
    if (n === ' ') return { key: ' ', code: 'Space' };
    return { key: n, code: '' };
  }
  switch (n.toLowerCase()) {
    case 'space':
      return { key: ' ', code: 'Space' };
    case 'enter':
      return { key: 'Enter', code: 'Enter' };
    case 'escape':
    case 'esc':
      return { key: 'Escape', code: 'Escape' };
    case 'backspace':
      return { key: 'Backspace', code: 'Backspace' };
    case 'tab':
      return { key: 'Tab', code: 'Tab' };
    default:
      return null;
  }
}

/** `ctrl+shift+space` → the event init a physical chord would produce. */
function chordInit(spec: string): KeyboardEventInit | null {
  const parts = spec.split('+').map((p) => p.trim()).filter(Boolean);
  const last = parts.pop() ?? '';
  const named = keyByName(last);
  if (!named) return null;
  const mods = parts.map((p) => p.toLowerCase());
  return {
    key: named.key,
    code: named.code,
    ctrlKey: mods.includes('ctrl'),
    shiftKey: mods.includes('shift'),
    altKey: mods.includes('alt'),
    metaKey: mods.includes('meta'),
    bubbles: true,
    cancelable: true,
  };
}

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

/** `tag.class` of whatever has focus, for the log. */
function describeActive(): string {
  const el = document.activeElement;
  if (!el) return 'none';
  const cls = el.className && typeof el.className === 'string' ? `.${el.className.split(' ')[0]}` : '';
  const place = el instanceof HTMLElement && el.dataset['place'] ? `[${el.dataset['place']}]` : '';
  return `${el.tagName.toLowerCase()}${cls}${place}`;
}

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
      window.tessa.respondToApproval(requestId, decision);
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
      const init = chordInit(step.arg);
      if (!init) {
        report(`DEV-DRIVE ${i} key "${step.arg}" BAD KEY (want e.g. ctrl+shift+M or ctrl+shift+space)`);
        continue;
      }
      const event = new KeyboardEvent('keydown', init);
      window.dispatchEvent(event);
      report(
        `DEV-DRIVE ${i} key ${step.arg} dispatched at window (defaultPrevented=${event.defaultPrevented}) ` +
          `active=${describeActive()}`,
      );
      continue;
    }

    /**
     * `press:<selector>~<chord>` — a keydown AND keyup ON AN ELEMENT.
     *
     * `key:` dispatches at `window`, which is where the app's chords listen.
     * The compose box listens on its own textarea through React's delegated
     * `onKeyDown`, and an event that starts at `window` never passes through
     * the root container that React listens on. Dispatching on the element
     * itself bubbles up through React's root exactly as a real keystroke
     * does, so Enter-to-send and Escape-to-clear run the real handlers.
     */
    if (step.action === 'press') {
      const bar = step.arg.lastIndexOf('~');
      const selector = bar < 0 ? step.arg.trim() : step.arg.slice(0, bar).trim();
      const chord = bar < 0 ? '' : step.arg.slice(bar + 1);
      const init = chordInit(chord);
      let target: EventTarget | null = null;
      if (selector === 'window') target = window;
      else {
        try {
          target = document.querySelector(selector);
        } catch {
          report(`DEV-DRIVE ${i} press "${selector}" INVALID SELECTOR`);
          continue;
        }
      }
      if (!init || !target) {
        report(`DEV-DRIVE ${i} press "${step.arg}" ${init ? 'NO MATCH' : 'BAD KEY'}`);
        continue;
      }
      const down = new KeyboardEvent('keydown', init);
      target.dispatchEvent(down);
      target.dispatchEvent(new KeyboardEvent('keyup', init));
      report(
        `DEV-DRIVE ${i} press ${chord} on "${selector}" (defaultPrevented=${down.defaultPrevented}) ` +
          `t=${performance.now().toFixed(1)} active=${describeActive()}`,
      );
      continue;
    }

    /**
     * `keys:<selector>~[<paceMs>~]<text>` — type CHARACTER BY CHARACTER, the
     * way a person does: keydown, the value grows by one, an `input` event,
     * keyup, then a pause. `type:` sets a whole value in one commit, which is
     * fine for a payload and useless for a keystroke-to-glyph measurement,
     * where every character has to be its own commit and its own frame.
     *
     * The pace defaults to 100 ms (≈120 wpm, a fast typist). Every character
     * runs the real React `onChange`; the box's own probe stamps the keydown
     * and reports the paint. Semicolons are not typable (`;` splits steps).
     */
    if (step.action === 'keys') {
      const first = step.arg.indexOf('~');
      const selector = first < 0 ? step.arg.trim() : step.arg.slice(0, first).trim();
      let rest = first < 0 ? '' : step.arg.slice(first + 1);
      let pace = 100;
      const second = rest.indexOf('~');
      if (second >= 0 && /^\d+$/.test(rest.slice(0, second))) {
        pace = Number.parseInt(rest.slice(0, second), 10);
        rest = rest.slice(second + 1);
      }
      let field: Element | null = null;
      try {
        field = document.querySelector(selector);
      } catch {
        report(`DEV-DRIVE ${i} keys "${selector}" INVALID SELECTOR`);
        continue;
      }
      if (!(field instanceof HTMLTextAreaElement) && !(field instanceof HTMLInputElement)) {
        report(`DEV-DRIVE ${i} keys "${selector}" NO MATCH (or not a field)`);
        continue;
      }
      field.focus();
      const started = performance.now();
      let typed = 0;
      for (const ch of rest) {
        const named = keyByName(ch) ?? { key: ch, code: '' };
        const down = new KeyboardEvent('keydown', { ...named, bubbles: true, cancelable: true });
        field.dispatchEvent(down);
        if (!down.defaultPrevented) setControlledValue(field, field.value + ch);
        field.dispatchEvent(new KeyboardEvent('keyup', { ...named, bubbles: true, cancelable: true }));
        typed += 1;
        await sleep(pace);
      }
      report(
        `DEV-DRIVE ${i} keys "${selector}" typed ${typed} chars at ${pace} ms in ` +
          `${(performance.now() - started).toFixed(0)} ms -> ${field.value.length} chars`,
      );
      continue;
    }

    /** `attr:<selector>~<name>` — one attribute, read back into the log. */
    if (step.action === 'attr') {
      const bar = step.arg.lastIndexOf('~');
      const selector = bar < 0 ? step.arg.trim() : step.arg.slice(0, bar).trim();
      const name = bar < 0 ? '' : step.arg.slice(bar + 1).trim();
      let node: Element | null = null;
      try {
        node = document.querySelector(selector);
      } catch {
        report(`DEV-DRIVE ${i} attr "${selector}" INVALID SELECTOR`);
        continue;
      }
      if (!node) {
        report(`DEV-DRIVE ${i} attr "${selector}" NO MATCH`);
        continue;
      }
      const value = node.getAttribute(name);
      const rect = node.getBoundingClientRect();
      report(
        `DEV-DRIVE ${i} attr "${selector}" ${name}=${JSON.stringify(value)} ` +
          `rect=${rect.left.toFixed(0)},${rect.top.toFixed(0)} ${rect.width.toFixed(0)}x${rect.height.toFixed(0)}`,
      );
      continue;
    }

    /** `count:<selector>` — how many match. The transcript line count, mostly. */
    if (step.action === 'count') {
      let n = -1;
      try {
        n = document.querySelectorAll(step.arg).length;
      } catch {
        report(`DEV-DRIVE ${i} count "${step.arg}" INVALID SELECTOR`);
        continue;
      }
      report(`DEV-DRIVE ${i} count "${step.arg}" = ${n} t=${performance.now().toFixed(1)}`);
      continue;
    }

    /** `active:` — what has focus right now, and whether the window does. */
    if (step.action === 'active') {
      report(`DEV-DRIVE ${i} active=${describeActive()} hasFocus=${document.hasFocus()}`);
      continue;
    }

    /** `focus:<selector>` — focus an element and say what has focus after. */
    if (step.action === 'focus') {
      let node: Element | null = null;
      try {
        node = document.querySelector(step.arg);
      } catch {
        report(`DEV-DRIVE ${i} focus "${step.arg}" INVALID SELECTOR`);
        continue;
      }
      if (!(node instanceof HTMLElement)) {
        report(`DEV-DRIVE ${i} focus "${step.arg}" NO MATCH`);
        continue;
      }
      node.focus();
      report(`DEV-DRIVE ${i} focus "${step.arg}" active=${describeActive()} hasFocus=${document.hasFocus()}`);
      continue;
    }

    /**
     * `capture:<name>` — ask main for ONE `capturePage()` now, under this
     * name. Main answers the metrics line; see IPC.devMetrics's handler. It
     * is how a capture lands on "after the ack, before the echo" rather than
     * on whatever the 500 ms cadence happened to hit.
     */
    if (step.action === 'capture') {
      report(`CAPTURE ${step.arg.trim()}`);
      report(`DEV-DRIVE ${i} capture ${step.arg.trim()} requested t=${performance.now().toFixed(1)}`);
      continue;
    }

    if (step.action === 'until') {
      const bar = step.arg.lastIndexOf('~');
      const selector = bar < 0 ? step.arg.trim() : step.arg.slice(0, bar).trim();
      const max = bar < 0 ? 10_000 : Number.parseInt(step.arg.slice(bar + 1), 10);
      const started = performance.now();
      let found = false;
      try {
        while (performance.now() - started < (Number.isFinite(max) ? Math.min(max, 60_000) : 10_000)) {
          if (document.querySelector(selector)) {
            found = true;
            break;
          }
          await sleep(10);
        }
      } catch {
        report(`DEV-DRIVE ${i} until "${selector}" INVALID SELECTOR`);
        continue;
      }
      report(`DEV-DRIVE ${i} until "${selector}" ${found ? 'matched' : 'TIMED OUT'} after ${(performance.now() - started).toFixed(0)} ms t=${performance.now().toFixed(1)} active=${describeActive()}`);
      continue;
    }

    if (step.action === 'scroll') {
      const [selector, deltaRaw, stepsRaw, paceRaw] = step.arg.split('~');
      let node: Element | null = null;
      try {
        node = document.querySelector(selector ?? '');
      } catch {
        node = null;
      }
      if (!(node instanceof HTMLElement)) {
        report(`DEV-DRIVE ${i} scroll "${step.arg}" NO MATCH`);
        continue;
      }
      const delta = Number.parseFloat(deltaRaw ?? '0');
      const count = Math.min(2000, Math.max(1, Number.parseInt(stepsRaw ?? '1', 10)));
      const pace = Math.min(1000, Math.max(0, Number.parseInt(paceRaw ?? '16', 10)));
      const started = performance.now();
      const top0 = node.scrollTop;
      for (let k = 0; k < count; k++) {
        node.scrollTop += delta;
        await sleep(pace);
      }
      report(`DEV-DRIVE ${i} scroll "${selector}" ${count} steps of ${delta}px every ${pace} ms: scrollTop ${top0.toFixed(0)} -> ${node.scrollTop.toFixed(0)} of ${node.scrollHeight} in ${(performance.now() - started).toFixed(0)} ms`);
      continue;
    }

    if (step.action === 'pclick' || step.action === 'kclick') {
      let node: Element | null = null;
      try {
        node = document.querySelector(step.arg);
      } catch {
        report(`DEV-DRIVE ${i} ${step.action} "${step.arg}" INVALID SELECTOR`);
        continue;
      }
      if (!(node instanceof HTMLElement)) {
        report(`DEV-DRIVE ${i} ${step.action} "${step.arg}" NO MATCH`);
        continue;
      }
      const disabled = node instanceof HTMLButtonElement ? node.disabled : false;
      if (step.action === 'pclick') {
        node.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerType: 'mouse', button: 0, isPrimary: true }));
        node.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerType: 'mouse', button: 0, isPrimary: true }));
        node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1, button: 0 }));
      } else {
        node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 0, button: 0 }));
      }
      report(`DEV-DRIVE ${i} ${step.action} "${step.arg}" dispatched disabled=${disabled} t=${performance.now().toFixed(1)} active=${describeActive()}`);
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
