"""
core/brain/executor.py — run a structured ToolCall and say what happened.

INVARIANT 4 IS ENFORCED BY SHAPE: this takes a tool NAME and an ARGS dict and
dispatches through a fixed table. There is no path here that accepts a command
string, so there is no path that could execute one.

THIS IS WHERE THE POSSESSIVE REGISTER FINALLY LIVES. `action_done()` and
`action_done(he_did_it_himself=True)` were written two prompts ago and nothing
ever called them — the character Gerald asked for has never once been heard. It
fires here, on ACTIONS only, and only when `memory.he_opened_it_himself()` has
real evidence: a Recent shortcut he created, and no record of her opening it.
No evidence means the plain confirmation. She does not perform the line.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from core.tools import REGISTRY
from core.tools.base import ToolError, ToolHold

from . import memory
from .approvals import ApprovalGate, red_refusal
from .confirm import ConfirmLedger
from .provenance import ExternalContent, InjectionRefusal
from .router import action_done, action_failed, destructive_hold
from .tools_local import (
    ToolCall,
    listening_on_port,
    open_in_vscode,
    open_path,
    open_url,
    tool_version,
)


class Executor:
    """
    `on_state` is called with 'working' before a tool runs, and with NOTHING
    after it.

    Local tools are instant so `working` flashes past; browser automation will
    take real seconds, and that is exactly when Gerald needs the sphere to show
    something other than the state it was in before he spoke.

    IT USED TO EMIT 'idle' IN A `finally`, AND THAT WAS WRONG. A tool is a step
    inside a turn, not the end of one — she still has to speak. Observed on the
    wire: listening -> thinking -> working -> IDLE -> speaking -> idle, with the
    stray idle lasting 216 ms between the folder opening and her answer. The Orb
    dwells 400 ms on a state, so that reads as the sphere going to sleep
    mid-sentence. The turn owns its terminal state: VoiceLoop sets SPEAKING
    after this returns, or IDLE itself when the answer is silence.
    """

    def __init__(self, on_state: Callable[[str], None] | None = None,
                 session: Any | None = None,
                 ledger: Any | None = None,
                 audit: Any | None = None,
                 on_permission_request: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._on_state = on_state
        # The injection fence (core/brain/provenance.SessionContext). Optional
        # so the executor stays unit-testable, but the daemon always supplies
        # one — see server.py.
        self.session = session
        self.ledger = ledger if ledger is not None else ConfirmLedger()
        self._audit = audit
        # CONTRACT §4.1 `evt.permission.request`. Optional so this stays
        # unit-testable — and when it is absent the red gate still REFUSES.
        # A missing surface must never become an open gate.
        self.approvals = ApprovalGate(on_request=on_permission_request)
        self.last_injection: dict[str, Any] | None = None

    def _state(self, s: str) -> None:
        if self._on_state is not None:
            self._on_state(s)

    def run(self, call: ToolCall) -> str:
        self._state("working")
        try:
            return self._dispatch(call)
        except InjectionRefusal as refusal:
            # NOT a generic failure, and it must not be reported as one. This is
            # the fence doing exactly its job, and he needs to hear that a page
            # tried something — not "that failed, sir".
            #
            # IT ALSO HAS TO TELL HIM THE WAY OUT. A refusal with no remedy is
            # indistinguishable from a broken tool, and he would reasonably
            # conclude the browser had bricked her hands. "Forget the page" is
            # his explicit act, it is auditable, and it is the only thing that
            # clears the flag.
            self._log("REFUSED", call.name, str(refusal))
            sources = ", ".join(getattr(self.session, "sources", []) or []) or "a web page"
            tried = ""
            if self.last_injection:
                tried = (f" That page also carried "
                         f"{len(self.last_injection['patterns'])} instruction-shaped "
                         f"pattern{'s' if len(self.last_injection['patterns']) != 1 else ''}, "
                         f"which I ignored.")
            return (f"No, Emperor. I have content from {sources} in front of me, and that "
                    f"was not a read-only action.{tried} Say forget the page and ask me again.")
        except Exception as exc:  # noqa: BLE001
            # Never a bare failure. zoey.md bans vagueness: name what broke and
            # offer the nearest real thing.
            return action_failed(f"{type(exc).__name__}: {exc}",
                                 "Tell me another way and I will try again.")

    # ── the answer to a hold ─────────────────────────────────────────────────

    def answer_confirmation(self, text: str) -> str | None:
        """
        Read a bare "yes" or "no" against a pending hold.

        Returns her line when the utterance WAS an answer, and None when it was
        not — and the None case is the important one. It is what lets him say
        "actually, what time is it" while a delete is held without that
        becoming a confirmation of the delete. Anything that is not recognisably
        an answer is routed normally and the hold simply stays pending until it
        expires.

        Called BEFORE routing, because "yes" routes to nothing and would
        otherwise come back as "I heard you, Emperor. Not that one yet."
        """
        verdict, held = self.ledger.resolve_utterance(text)
        if held is None or verdict == "none":
            return None
        if verdict == "cancel":
            self._log("CANCELLED", held.tool, held.detail)
            return "Left it, Emperor. Nothing happened."
        args = dict(held.args)
        args["confirmed"] = True
        # `run`, NOT `_dispatch`. THIS LINE WAS A SILENT-FAILURE BUG.
        #
        # `_dispatch` has no exception handling; `run` owns it. The reachable
        # path: he says "kill 4242" (amber, arms the hold while the fence is
        # empty) -> he asks her to read a web page (green, sets
        # external_content_in_context=1) -> he says "yes". The fence then
        # refuses the amber action correctly, but InjectionRefusal escaped
        # `answer_confirmation`, escaped VoiceLoop.stop() — whose try/except
        # wraps the ROUTED tool loop, not the confirmation call — and landed in
        # server.py's generic handler, which logs "voice turn failed", drops the
        # sphere to idle and SYNTHESISES NOTHING.
        #
        # So the one path where the injection defence actually engaged was the
        # one where she said nothing at all: silence, a consumed hold, and a
        # generic crash record instead of the REFUSED audit entry. Silence is
        # indistinguishable from a crash, which is the failure mode this whole
        # codebase keeps designing against.
        #
        # Routing through `run` also emits `working` before the confirmed action,
        # which is the correct shape — confirming a kill IS work.
        return self.run(ToolCall(name=held.tool, args=args))

    # ── the red gate and the fence ───────────────────────────────────────────

    @staticmethod
    def _audit_line(spec: Any, args: dict[str, Any]) -> str:
        """
        Format an audit template over ARGS, tolerating the ones that are absent.

        `browser.read_page`'s template is "read page {url}" and its `url`
        argument is OPTIONAL — calling it with no url to re-read the current
        page raised KeyError inside the audit call, which surfaced to Gerald as
        `"It did not open, sir. KeyError: 'url'."` on a read that had in fact
        completely succeeded. An audit line must never be able to fail a tool
        that worked.
        """
        class _Missing(dict):
            def __missing__(self, key: str) -> str:  # noqa: D105
                return "-"

        try:
            return spec.audit.format_map(_Missing(name=spec.name, **args))
        except Exception:  # noqa: BLE001
            return spec.name

    @staticmethod
    def _red_detail(spec: Any, args: dict[str, Any]) -> str:
        """
        What she names back to him. The SPECIFIC target, never the category —
        "delete C:\\Users\\...\\old" and not "a delete", because the whole point
        of an approval is that he can see what he is approving.
        """
        for key in ("path", "command", "text", "url", "name"):
            if key in args and args[key]:
                return f"{spec.name} on {str(args[key])[:120]}"
        return spec.name

    def _absorb_external(self, spec: Any, result: dict[str, Any]) -> None:
        """
        Load a tool's outside-world output into the fence.

        THE ACCESSIBILITY TREE IS IN HERE, and that is the gap this closes. A
        page can hide an instruction in `aria-label`, in `alt`, in a
        `display:none` div, or in the accessible NAME of a button — and
        `browser.click` selects elements BY that name, so the accessibility
        tree is not an obscure corner, it is the exact channel one of her tools
        reads from. `browser.read_page` harvests all of it into one string and
        this loads that string, so a name-based injection is fenced and counted
        like any other external content.
        """
        if self.session is None or not isinstance(result, dict):
            return
        text = result.get("external_text")
        if not text:
            return
        source = str(result.get("external_source") or spec.name)
        fired = self.session.load_external(ExternalContent(source=source, text=str(text)))
        if fired:
            self._log("INJECTION-SEEN", spec.name,
                      f"{len(fired)} pattern(s) in content from {source}: {fired}",
                      spec.tier)
            self.last_injection = {"source": source, "patterns": fired}

    # ── audit ────────────────────────────────────────────────────────────────

    def _log(self, verb: str, tool: str, summary: str, tier: str = "green") -> None:
        if self._audit is None:
            return
        try:
            self._audit.append(actor="human", tool=tool, tier=tier,
                               summary=f"{verb} {summary}", detail={})
        except Exception:  # noqa: BLE001
            pass    # an audit failure must never take a turn down with it

    # ── the registry path ────────────────────────────────────────────────────

    def _dispatch_registry(self, call: ToolCall) -> str | None:
        """
        Every tool in `core/tools`. Returns None when the name is not one of
        them, so the legacy `app.*` branches below still run.

        THE ORDER INSIDE THIS FUNCTION IS THE SECURITY ORDER:
          1. fence   — a red OR AMBER action dies here while a page is in context
          2. red gate— a red action never executes on voice; it raises a request
          3. hold    — an amber holding tool asks before it acts
          4. run     — only now does anything happen
          5. re-fence— anything that READ the outside world loads its output
                       back into the fence, so the NEXT action is constrained
        Doing the fence check after the handler would mean the folder is
        already deleted by the time she refuses.
        """
        spec = REGISTRY.get(call.name)
        if spec is None:
            return None

        args = dict(call.args)

        # 0. THE WAY OUT, and it is checked BEFORE the fence on purpose. If
        #    `context.forget` were itself gated by the flag it clears, the block
        #    would be permanent and the only escape would be restarting the
        #    daemon. A control with no release is a fault, not a safeguard.
        if spec.name == "context.forget":
            had = getattr(self.session, "external_content_in_context", 0) if self.session else 0
            srcs = list(getattr(self.session, "sources", []) or []) if self.session else []
            if self.session is not None:
                self.session.clear_external()
            self.last_injection = None
            self._log("CLEARED-EXTERNAL", spec.name,
                      f"{had} source(s) dropped: {', '.join(srcs) or 'none'}", "green")
            return spec.success

        # 1. THE FENCE. Raises InjectionRefusal, caught in run().
        if self.session is not None:
            self.session.check_tool(spec.name, spec.tier)

        # 2. THE RED GATE. A red tool NEVER executes from a voice turn, however
        #    many times he says yes. It raises a permission request and stops.
        #    See core/brain/approvals.py for why voice is not an approval
        #    surface, and for what this changed about fs.delete and
        #    shell.execute, which used to execute on a spoken confirmation.
        if spec.tier == "red" and not args.get("_approved_by_surface"):
            detail = self._red_detail(spec, args)
            req = self.approvals.request(
                tool=spec.name, args=args, tier="red",
                provenance=str(args.get("provenance", "human")), detail=detail,
            )
            self._log("PENDING-APPROVAL", spec.name,
                      f"requestId={req.request_id} {self._audit_line(spec, args)}",
                      "red")
            # DROP ANY PENDING AMBER HOLD. Without this: he says "kill 4242"
            # (hold armed), then "tweet that we shipped" (red, refused, and she
            # says "I have it ready... it is logged and waiting"), then "yes" —
            # believing he is authorising the tweet — and the yes lands on the
            # KILL instead. Two different destructive actions, one ambiguous
            # word between them.
            #
            # A red refusal moves the conversation on, so the older hold is no
            # longer something he can be assumed to mean. He re-issues it; that
            # costs one sentence and removes the ambiguity entirely.
            if self.ledger.pending is not None:
                self._log("HOLD-DROPPED", self.ledger.pending.tool,
                          "a red refusal made a pending 'yes' ambiguous", "amber")
                self.ledger.clear()
            return red_refusal(spec.name, detail)

        # 3. THE HOLD — AMBER ONLY now. A repeat of the same command IS the
        #    confirmation she promised out loud (core/brain/confirm.py), and it
        #    is still the right control for `proc.kill`: he named a PID, the act
        #    is instant, and nothing leaves the machine.
        if spec.holds and not args.get("confirmed"):
            if self.ledger.resolve_repeat(spec.name, args) is not None:
                args["confirmed"] = True
            # else: fall through, the handler raises ToolHold and we arm below.

        try:
            result = spec.handler(**args)
        except ToolHold as hold:
            self.ledger.arm(spec.name, args, hold.detail)
            self._log("HELD", spec.name, self._audit_line(spec, args), spec.tier)
            return destructive_hold(hold.detail)
        except ToolError as err:
            self._log("FAILED", spec.name, f"{err.reason}", spec.tier)
            # Capitalised: the template puts the reason after a full stop, and
            # "I could not read your timeline, sir. you have not signed in"
            # reads as a template leaking rather than as her speaking.
            reason = err.reason.rstrip(". ")
            reason = (reason[:1].upper() + reason[1:] + ".") if reason else "."
            return spec.failure.format(reason=reason, alternative=err.alternative)

        # 5. RE-FENCE. Any tool that reached outside this machine — a page, a
        #    search, a timeline, a clipboard, a file — hands back its output
        #    under `external_text`. Loading it here is what sets
        #    `external_content_in_context`, so the tool call AFTER a page read
        #    is the one that gets constrained. Doing this in the handlers would
        #    mean every new tool has to remember; doing it here means none of
        #    them can forget.
        self._absorb_external(spec, result)

        self._log("ran", spec.name, self._audit_line(spec, args), spec.tier)
        try:
            return spec.success.format(**result)
        except (KeyError, IndexError, ValueError) as bad:
            # A success template referencing a field the handler did not return
            # is a bug in the SPEC, not a reason to go silent on him — so she
            # still answers.
            #
            # BUT IT IS LOGGED LOUDLY, because the silent version of this hid a
            # real defect: `fs.list` promised "{n} items in {name}" and its
            # handler returned no `name`, so every folder listing came back as
            # the generic "There you go, Emperor." and looked deliberate. A
            # fallback that cannot be distinguished from success is how a
            # degraded surface stays degraded.
            self._log("SPEC-BUG", spec.name,
                      f"success template {spec.success!r} wants {bad}, "
                      f"handler returned {sorted(result)}", spec.tier)
            print(f"  !! tool spec bug: {spec.name} success template wants {bad}",
                  file=__import__("sys").stderr)
            return action_done()

    #: The tool names that predate `core/tools/REGISTRY` and are still served by
    #: the hand-written branches below.
    #:
    #: THIS MAP EXISTS BECAUSE THOSE BRANCHES HAD NO TIER AT ALL, and therefore
    #: never reached `session.check_tool` and never reached the audit log.
    #: Demonstrated, not theorised: with a hostile page loaded into the fence,
    #: `app.open_folder` answered "Open, Emperor." and opened the folder — and
    #: `app.open_folder` is the single most common command in this daemon.
    #: `_validate()` could not see the gap either, because it only inspects the
    #: registry it is given.
    #:
    #: The tiers here are the same ones permissions.yaml assigns to the
    #: equivalent capability, so nothing changes for green tools. What changes
    #: is that `sys.kill_port` is now gated like the amber action it is, and
    #: every one of them is audited.
    _LEGACY_TIERS = {
        "app.open_folder": "green", "app.open": "green",
        "app.open_vscode": "green", "app.open_url": "green",
        "sys.port_owner": "green", "sys.tool_version": "green",
        "sys.disk": "green", "sys.memory": "green",
        "sys.battery": "green", "sys.uptime": "green",
        "sys.process_list": "green", "sys.top_processes": "green",
        "sys.volume": "green", "sys.media": "green", "sys.lock": "green",
        "sys.kill_port": "amber",
    }

    def _dispatch(self, call: ToolCall) -> str:
        name, args = call.name, call.args

        via_registry = self._dispatch_registry(call)
        if via_registry is not None:
            return via_registry

        # THE LEGACY TAIL IS GATED AND AUDITED TOO. An unknown name gets the
        # most restrictive tier rather than none: a tool this map has not heard
        # of is exactly the one nobody has thought about.
        tier = self._LEGACY_TIERS.get(name, "red")
        if self.session is not None:
            self.session.check_tool(name, tier)
        self._log("ran(legacy)", name, f"{name} {args}", tier)

        if name == "app.open_folder":
            path = Path(str(args["path"]))
            if not path.exists():
                return action_failed(f"{path} is not there",
                                     "Give me another path and I will open it.")
            himself = memory.he_opened_it_himself(str(path))
            open_path(path)
            memory.record(name, str(path))
            return action_done(he_did_it_himself=himself)

        if name == "app.open":
            app = str(args["app"])
            himself = memory.he_opened_it_himself(app)
            from .tools_local import index_start_menu
            lnk = index_start_menu().get(app)
            if lnk is None:
                return action_failed(f"I cannot find {app}", "Say the name again?")
            open_path(lnk)
            memory.record(name, app)
            return action_done(he_did_it_himself=himself)

        if name == "app.open_vscode":
            target = str(args.get("path") or "")
            if not target:
                exe = shutil.which("code") or shutil.which("code.cmd")
                if exe is None:
                    return action_failed("VS Code is not on PATH",
                                         "I can open the folder in Explorer instead.")
                subprocess.Popen([exe], shell=False)
                memory.record(name, "vscode")
                return action_done()
            ok, detail = open_in_vscode(Path(target))
            if not ok:
                return action_failed(detail, "I can open it in Explorer instead.")
            himself = memory.he_opened_it_himself(target)
            memory.record(name, target)
            return action_done(he_did_it_himself=himself)

        if name == "app.open_url":
            ok, detail = open_url(str(args["url"]), args.get("browser"))
            if not ok:
                return action_failed(detail, "Give me a full web address.")
            memory.record(name, str(args["url"]))
            return action_done()

        if name == "sys.port_owner":
            port = int(args["port"])
            rows = listening_on_port(port)
            if not rows:
                return f"Nothing is on port {port}, Emperor."
            r = rows[0]
            return f"Port {port} is {r['name']}, Emperor. Process {r['pid']}."

        if name == "sys.tool_version":
            ok, detail = tool_version(str(args["tool"]))
            if not ok:
                return action_failed(detail, "It may not be installed.")
            return f"{detail}, Emperor."

        if name in ("sys.disk", "sys.memory", "sys.battery", "sys.uptime"):
            return self._machine(name)

        if name in ("sys.process_list", "sys.top_processes"):
            return self._processes(int(args.get("n", 5)))

        if name in ("sys.volume", "sys.media", "sys.lock"):
            return self._control(name, args)

        if name == "sys.kill_port":
            # AMBER, and it holds. The executor never kills on the first ask —
            # confirmation is the caller's to obtain (zoey.md: she says it and
            # HOLDS, he confirms a second time).
            port = int(args["port"])
            rows = listening_on_port(port)
            if not rows:
                return f"Nothing is on port {port}, Emperor. Nothing to kill."
            r = rows[0]
            from .router import destructive_hold
            return destructive_hold(
                f"Port {port} is {r['name']}, process {r['pid']}")

        return action_failed(f"{name} is not wired yet", "Ask me something else.")

    # ── read-only machine state ──────────────────────────────────────────────

    def _machine(self, name: str) -> str:
        import psutil

        if name == "sys.disk":
            u = psutil.disk_usage("C:\\")
            return (f"{u.free / 1e9:.1f} gigabytes free, Emperor. "
                    f"That is {100 - u.percent:.0f} percent of the drive.")
        if name == "sys.memory":
            m = psutil.virtual_memory()
            return (f"{m.available / 1e9:.1f} gigabytes free, Emperor. "
                    f"{m.percent:.0f} percent in use.")
        if name == "sys.battery":
            b = psutil.sensors_battery()
            if b is None:
                return "I cannot read a battery on this machine, sir."
            plugged = "on mains" if b.power_plugged else "on battery"
            return f"{b.percent:.0f} percent, Emperor. You are {plugged}."
        seconds = int(psutil.time.time() - psutil.boot_time())
        hours, rem = divmod(seconds, 3600)
        return f"Up {hours} hours and {rem // 60} minutes, Emperor."

    def _processes(self, n: int) -> str:
        import psutil

        rows: list[tuple[float, str]] = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                rows.append((p.info["memory_info"].rss, p.info["name"]))
            except Exception:  # noqa: BLE001
                continue
        rows.sort(reverse=True)
        top = ", ".join(f"{nm} at {rss / 1e6:.0f} megabytes" for rss, nm in rows[:n])
        return f"Heaviest first, Emperor. {top}."

    def _control(self, name: str, args: dict[str, Any]) -> str:
        # Windows media/volume keys, sent through the shell's own key API.
        # Structured constants, never a string from anywhere else.
        import ctypes

        VK = {"up": 0xAF, "down": 0xAE, "mute": 0xAD,
              "playpause": 0xB3, "next": 0xB0}
        if name == "sys.lock":
            ctypes.windll.user32.LockWorkStation()
            return "Locking, Emperor."
        key = VK.get(str(args.get("direction") or args.get("action") or ""))
        if key is None:
            return action_failed("I did not catch which control", "Say it again?")
        ctypes.windll.user32.keybd_event(key, 0, 0, 0)
        ctypes.windll.user32.keybd_event(key, 0, 2, 0)
        return action_done()
