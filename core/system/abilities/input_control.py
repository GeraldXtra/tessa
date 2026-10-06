"""
core/system/abilities/input_control.py — the keyboard and the mouse.

    system.input.type       AMBER   input.keyboard   type text into the focused window
    system.input.key        AMBER   input.keyboard   press a key or a combination
    system.input.mouse      AMBER   input.mouse      move / click / double / right / drag
    system.input.scroll     GREEN   input.scroll     turn the wheel

────────────────────────────────────────────────────────────────────────────────
⚠⚠ WHY THE KEYBOARD IS THE MOST DANGEROUS THING IN THIS BATCH

`shell.execute` is red and deliberately unbuilt — running an arbitrary command
is the master key and it gets its own round. A synthetic keyboard is a way
AROUND that decision, and a short one: type into an open terminal, or press
Win+R and type into the box. Either reaches the isolated capability through a
door nobody reviewed.

Three things bound it, and none of them is a promise in a docstring:

  1. THE MECHANISM REFUSES A TERMINAL. `winapi/inputs.py::keyboard_target()`
     reads the foreground window's CLASS — which a window cannot spoof the way
     it can spoof a title — and refuses `ConsoleWindowClass`, Windows Terminal,
     mintty and PuTTY. The refusal is in `send_text`/`send_keys` themselves, so
     a capability added next month inherits it by calling them.

  2. THE RUN-BOX COMBINATIONS ARE REFUSED OUTRIGHT, not held. A hold cannot
     help here: "shall I press Win+R" tells him nothing about what would be
     typed into the box a second later, so there is no informed yes to give.

  3. AMBER MEANS HE SAYS YES EVERY TIME, and the hold names the actual text.
     "Shall I type: rm -rf" is a question he can answer; "shall I type
     something" is not, which is why `hold` interpolates the argument.

⚠ THE LIMIT, STATED HERE AND IN THE REPORT: VS Code's integrated terminal is an
Electron surface with the same window class as its editor. Refusing that class
would refuse dictation into the editor, which is one of the two places he wants
it. So the bar stops the real terminals and does not stop an embedded one. It
is a bar across the obvious door, not a proof of impossibility.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param


def _refuse(exc: Exception, alternative: str) -> Any:
    from core.tools.base import ToolError

    raise ToolError(str(exc), alternative) from None


def type_text(text: str) -> dict[str, Any]:
    """AMBER. Type into whatever has focus — after the mechanism approves it."""
    from core.system.winapi import inputs

    try:
        got = inputs.send_text(text)
    except inputs.InputRefused as exc:
        _refuse(exc, "Put the cursor where you want it and ask me again.")
    # ⚠ SHE SAYS WHERE IT WENT. Typing goes to whatever holds focus, and focus
    # can move between him asking and him confirming — a proof in this very
    # round typed a test string into a document that had quietly kept the focus.
    # Nothing was lost, and it was a bug in the proof rather than in this
    # handler, but the incident showed that "Typed 23 characters" is not an
    # answer he can check. Naming the window makes a wrong target obvious in the
    # one second afterwards, when Ctrl+Z still fixes it.
    where = got.get("window") or "the focused window"
    return {**got, "text": text[:60],
            "verdict": f"Typed {got['chars']} characters into {where}, Emperor."}


def press_key(keys: str) -> dict[str, Any]:
    """AMBER. "enter", "ctrl+c", "alt+tab" — from the closed key table."""
    from core.system.winapi import inputs

    parts = [p for p in str(keys or "").replace(" plus ", "+").replace(" ", "+").split("+") if p]
    try:
        got = inputs.send_keys(parts)
    except inputs.InputRefused as exc:
        _refuse(exc, "Tell me what you want to happen and I will find another way.")
    where = got.get("window") or "the focused window"
    return {**got, "verdict": f"Pressed {got['keys']} in {where}, Emperor."}


def mouse(action: str = "click", x: int = -1, y: int = -1,
          to_x: int = -1, to_y: int = -1, button: str = "left") -> dict[str, Any]:
    """AMBER. Move, click, double-click, right-click or drag."""
    from core.system.winapi import inputs

    try:
        moved = {}
        if x >= 0 and y >= 0 and action != "drag":
            moved = inputs.move_to(x, y)
        if action == "move":
            return {**moved, "action": "move", "button": "",
                    "verdict": f"Cursor at {moved.get('x')}, {moved.get('y')}, Emperor."}
        if action == "drag":
            if min(x, y, to_x, to_y) < 0:
                _refuse(ValueError("a drag needs a start and an end"),
                        "Say drag from one point to another.")
            got = inputs.drag(x, y, to_x, to_y, button)
            return {**got, "action": "drag", "button": button,
                    "verdict": f"Dragged to {to_x}, {to_y}, Emperor."}
        got = inputs.click(button=("right" if action == "right_click" else button),
                           double=(action == "double_click"))
        return {**got, "action": action,
                "verdict": f"{action.replace('_', ' ').capitalize()} at "
                           f"{got['x']}, {got['y']}, Emperor."}
    except inputs.InputRefused as exc:
        _refuse(exc, "Give me a point on the screen.")
    except KeyError:
        _refuse(ValueError(f"{button!r} is not a mouse button I have"),
                "Left, right or middle.")


def scroll(amount: int = -3, horizontal: bool = False) -> dict[str, Any]:
    """
    GREEN. Turning the wheel changes nothing and undoes itself.

    The one input act with no state change behind it: scrolling a window moves a
    viewport. Nothing is typed, nothing is clicked, nothing is committed, and
    scrolling back is exactly as easy. That is the whole argument for green.
    """
    from core.system.winapi import inputs

    got = inputs.scroll(amount, horizontal)
    way = "down" if amount < 0 else "up"
    return {**got, "verdict": f"Scrolled {way}, Emperor."}


CAPABILITIES = [
    Capability(
        name="system.input.type", capability="input.keyboard", tier="amber",
        run=type_text,
        params=(Param("text", str, doc="What should I type?"),),
        phrasings=("type hello world", "type this out", "write that into the window"),
        success="{verdict}",
        audit="TYPE {text} into the focused window",
        hold="type {text}",
        note="AMBER. The hold names the ACTUAL TEXT, because 'shall I type something' "
             "is not a question he can answer. The mechanism refuses a terminal window "
             "by class before a single character is sent — typing into a terminal is "
             "`shell.execute`, which is not built and gets its own round.",
    ),
    Capability(
        name="system.input.key", capability="input.keyboard", tier="amber",
        run=press_key,
        params=(Param("keys", str, doc="Which key, or which combination?"),),
        phrasings=("press enter", "hit control c", "alt tab", "press escape"),
        success="{verdict}",
        audit="KEY {keys}",
        hold="press {keys}",
        note="AMBER, from a CLOSED key table. Win+R, Win+X, Ctrl+Shift+Esc, Alt+F4 and "
             "any unlisted Windows-key combination are REFUSED OUTRIGHT rather than held: "
             "a hold cannot make them safe, because he cannot see from 'shall I press "
             "Win+R' what would be typed into the box afterwards.",
    ),
    Capability(
        name="system.input.mouse", capability="input.mouse", tier="amber",
        run=mouse,
        params=(Param("action", str, default="click",
                      choices=("move", "click", "double_click", "right_click", "drag"),
                      doc="Move, click, double click, right click or drag?"),
                Param("x", int, default=-1, doc="Where across?"),
                Param("y", int, default=-1, doc="Where down?"),
                Param("to_x", int, default=-1, doc="Drag to where across?"),
                Param("to_y", int, default=-1, doc="Drag to where down?"),
                Param("button", str, default="left", choices=("left", "right", "middle"))),
        phrasings=("click", "double click", "right click", "click at 400 300", "drag to"),
        success="{verdict}",
        audit="MOUSE {action} at {x},{y}",
        hold="{action} at {x},{y}",
        note="AMBER. A click lands on whatever is under the cursor, and she cannot see "
             "what that is — a Delete button and a Cancel button are the same act to "
             "her. So the owner confirms the coordinates.",
    ),
    Capability(
        name="system.input.scroll", capability="input.scroll", tier="green",
        run=scroll,
        params=(Param("amount", int, default=-3, lo=-30, hi=30, doc="How many notches?"),
                Param("horizontal", bool, default=False)),
        phrasings=("scroll down", "scroll up", "scroll down a bit", "scroll to the bottom"),
        success="{verdict}",
        audit="SCROLL {amount}",
        note="GREEN, and the only green input act. A wheel turn moves a viewport: it "
             "commits nothing, types nothing and undoes itself by scrolling back.",
    ),
]
