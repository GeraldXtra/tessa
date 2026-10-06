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

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, get_args

from core.security.audit import Actor as _AuditActor
from core.tools import REGISTRY
from core.tools.base import ToolError, ToolHold

from . import memory
from .approvals import ApprovalError, ApprovalGate, red_refusal, resolve_edit
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


#: TOOLS WHOSE RESULT `posts` ARE STRANGERS' X POSTS, and so feed the two
#: learning stores AFTER the fence (`_note_claims`, `_absorb_style`). A name
#: prefix, so the reading round's `x.read_thread` and `x.read_user` joined
#: with no change here; `x.search` is the same kind of read under a
#: different verb and is listed so it takes the SAME path — not a new one.
_X_READ_PREFIXES = ("x.read", "x.search")


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
                 on_permission_request: Callable[[dict[str, Any]], None] | None = None,
                 claims: Any | None = None,
                 style: Any | None = None,
                 relevance: Any | None = None) -> None:
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
        # THE CLAIM STORE (core/brain/claims.py). Optional, like the fence:
        # with none supplied an X read is fenced and NOT noted, which is the
        # safe direction. The daemon supplies its one store — see server.py.
        self.claims = claims
        # THE STYLE STORE (core/brain/style.py). Optional, same rule: with
        # none supplied an X read is fenced, noted, and teaches nothing about
        # how people write. It holds numbers and single words only — see the
        # module — so it can shape how she phrases and cannot hold a claim.
        self.style = style
        # THE SELECTIVE FILTER (core/brain/relevance.py). Optional, and the
        # default runs the OTHER way from the two stores': with none supplied
        # EVERY post of an X read is noted, which is Stage 1 exactly and what
        # the claim and style suites drive. It decides note-or-skip and
        # nothing else: it cannot set a status, and a post cannot talk its
        # way through it, because it is handed the post's text and answers a
        # bool. See `_note_claims`.
        self.relevance = relevance
        # POWER ACTIONS REPORT THROUGH THIS EXECUTOR'S AUDIT. An approved
        # shutdown fires from a timer thread after the approval turn has
        # returned, and a cancel undoes one — both happen with no call in
        # flight, so core/system/abilities/power.py is handed `_log` here and
        # writes SCHEDULED / FIRED / CANCELLED with the request's true actor.
        # NOT A GATE: nothing here decides whether anything runs, and nothing
        # here reads args. The card path above is unchanged.
        try:
            from core.system.abilities import power as _power
            _power.bind_reporter(self._log)
            # Same shape for the software-change round (2026-09-12): RAN /
            # RAN-FAILED with the exact winget argv and exit code, true actor.
            from core.system.abilities import software_change as _software
            _software.bind_reporter(self._log)
            # And the machine-control round (2026-09-12): a registry write
            # and a service start/stop/config each put the EXACT target on
            # the chain — key\name = value, or sc <verb> <Name> and its exit
            # code — with RUNNING / RAN / RAN-FAILED and the true actor.
            # Without this their `bind_reporter` is dead code and an
            # approved-but-FAILED write leaves nothing behind.
            from core.system.abilities import registry_write as _registry
            _registry.bind_reporter(self._log)
            from core.system.abilities import service_control as _service
            _service.bind_reporter(self._log)
        except Exception:  # noqa: BLE001
            pass
        # And the X publishing round 3 (2026-09-12): a thread posts N parts
        # one after another, each chained to the id X handed back for the
        # part before, and the REQUESTED / APPROVED pair below is written
        # BEFORE the handler runs. So each part reports THREAD-PART k/n with
        # its id AS IT LANDS, and a part that fails leaves THREAD-STOPPED
        # with the ids that ARE public — never two public posts and silence.
        # Same shape as the four above; not a gate, reads no args.
        try:
            from core.tools import x_tools as _x_tools
            _x_tools.bind_reporter(self._log)
        except Exception:  # noqa: BLE001
            pass

    def _state(self, s: str, detail: dict | None = None) -> None:
        if self._on_state is None:
            return
        try:
            self._on_state(s, detail)
        except TypeError:
            self._on_state(s)

    @staticmethod
    def state_detail(call: ToolCall) -> dict:
        """
        CONTRACT §4.1 `evt.agent.state.detail` — `{ tool?, target?, note? }`.

        REDACTED BEFORE IT LEAVES, AND THIS IS NOT OPTIONAL.

        `evt.agent.state` is broadcast to EVERY subscriber. The audit log
        redacts secrets before write (CLAUDE.md invariant 7), and this event
        would otherwise walk straight past that redactor: `shell.execute` args
        carry command lines, `browser.open_url` carries URLs, and either can
        contain a token. A surface will render this field and may persist it.
        So the same `redact()` the audit log uses runs here, on the way out.

        TRUNCATED TOO. 120 characters is enough to recognise a path and short
        enough that a pasted blob cannot ride out inside a status event.

        `target` reuses `_red_detail`'s key order — path, command, text, url,
        name — which is the same question ("what is this action ABOUT") asked
        for the approval card. Session 2 read that as a field copy; it is one
        function call, because `_red_detail` only ran on the red-tier path.
        """
        from core.security.audit import redact

        args = dict(call.args or {})
        # A PRIVATE argument never rides out in a state event either (round
        # 4): `x.send_dm`'s message is for the card, not for every subscriber.
        for _key in Executor._private_args_of(call.name):
            args.pop(_key, None)
        target = ""
        for key in ("path", "command", "text", "url", "name", "app", "query"):
            val = args.get(key)
            if val:
                target = str(val)
                break

        detail: dict[str, Any] = {"tool": call.name}
        if target:
            safe = redact(target[:120])
            detail["target"] = safe if isinstance(safe, str) else str(safe)
        return detail

    def run(self, call: ToolCall) -> str:
        self._state("working", self.state_detail(call))
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
            self._log("REFUSED", call.name, str(refusal), self._tier_of(call.name),
                      actor=self._actor_of(call.origin))
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
            # Never a bare failure. tessa.md bans vagueness: name what broke and
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
            # The held ACTION's initiator, not the voice that said "no" — the
            # entry is about `held.tool`, and who asked for it is the question.
            self._log("CANCELLED", held.tool, held.detail, self._tier_of(held.tool),
                      actor=self._actor_of(getattr(held, "origin", None)))
            return "Left it, Emperor. Nothing happened."
        args = dict(held.args)
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
        # `origin=held.origin`: the re-dispatched call carries the origin the
        # ORIGINAL arrived with. Without this a confirmed hold silently became
        # a "human" call regardless of who built it.
        # `confirmed=True` ON THE CALL, NOT IN `args`. The args key is stripped
        # at dispatch now (it was forgeable — see `_FORGEABLE_ARG_KEYS`), so
        # his genuine "yes" travels on the field a model's args cannot reach.
        # The ledger has already accepted and consumed the hold above; this is
        # the executor telling itself so, out of band.
        return self.run(ToolCall(name=held.tool, args=args,
                                 origin=getattr(held, "origin", "schedule"),
                                 confirmed=True))

    # ── the approval path: a decision arriving from a surface ────────────────

    def execute_approved(self, request_id: str, edited: Any = None) -> dict[str, Any]:
        """
        Run a red-tier action the OWNER approved on a surface he can see.

        This is the only route by which a red tool ever executes. It is reached
        from `cmd.permission.respond` (CONTRACT §5.1) and from nowhere else —
        not from a voice confirmation, not from a repeat, not from the ledger.

        THE FENCE RULING IS HERE, AND IT IS DELIBERATE. See `_ruling` below.

        Returns a record for the caller to audit and broadcast. Raises
        `ApprovalError` for anything it will not honour.
        """
        # ── CLAIM IT ATOMICALLY, BEFORE ANYTHING ELSE ────────────────────────
        #
        # `pop` rather than `get`. The first version read the request, ran the
        # handler, and popped afterwards — a check-then-act window in a daemon
        # where the handler runs under `asyncio.to_thread`, so two
        # `cmd.permission.respond` frames carrying the same requestId could both
        # pass the lookup and BOTH EXECUTE. On `x.post` that is the same tweet
        # published twice; on `fs.delete` it is a second delete against a path
        # that may since have been recreated.
        #
        # `dict.pop` is atomic under the GIL, so the loser of the race gets None
        # and is told `notFound`. Claiming first also fixes the other half: a
        # handler that raises something other than ToolError used to leave the
        # request pending and replayable.
        #
        # Anything that refuses BEFORE execution puts it back, so a rejected
        # edit does not cost him the card.
        pending = self.approvals.pending.pop(request_id, None)
        if pending is None:
            raise ApprovalError("notFound", f"no pending approval {request_id}")
        if pending.expired:
            raise ApprovalError("permission.expired",
                                "that approval window has closed")

        # WHO INITIATED THIS ACTION — the value the card was shown. `pending.
        # provenance` is `_actor_of(call.origin)` as resolved at dispatch and
        # stored on the request, so the chain records the same initiator the
        # owner approved against. Resolved again here only so a request built
        # by hand with an unrecognised value cannot put a bogus actor on the
        # chain; for anything that came through `_dispatch_registry` this is
        # the identity function. His approval is a human act; the ACTION was
        # still initiated by whoever built the call, and that is what `actor`
        # means on every entry below.
        actor = self._actor_of(pending.provenance)

        spec = REGISTRY.get(pending.tool)
        if spec is None:
            # `pty.spawn` reaches here: the PTY grant flow raises a real approval
            # request but its EXECUTION lives in the grant registry, not in
            # `core/tools`. Denying works; approving does not, and it says so
            # rather than failing as though the request had gone stale.
            raise ApprovalError(
                "internal",
                f"{pending.tool} approval is not wired to an executor yet - "
                f"deny works, approve does not")

        # Raises ApprovalError on anything it will not accept. The TOOL and the
        # TIER come from `pending`, never from the frame.
        #
        # A REJECTED EDIT PUTS THE REQUEST BACK. He mistyped, or the surface sent
        # something malformed — the decision failed, the REQUEST did not, and
        # destroying the card would make him re-dictate a tweet he has already
        # corrected once.
        try:
            args = resolve_edit(pending, edited)
        except ApprovalError:
            self.approvals.pending[request_id] = pending
            raise
        was_edited = args != pending.args

        # `pending.args` were stripped at dispatch, and `resolve_edit` refuses
        # keys the request did not have — so nothing should be here. Stripped
        # again regardless: the audit lines below record these args verbatim,
        # and an impersonation key must never reach the chain by any route.
        self._strip_forgeable(spec.name, args, actor=actor, tier=spec.tier)

        # ── THE FENCE AND THE APPROVAL ───────────────────────────────────────
        #
        # A red action requested while a hostile page was in context is refused
        # by `SessionContext.check_tool`. Does his approval clear that?
        #
        # YES — AND ONLY FOR THIS ONE REQUEST.
        #
        # The reason it is safe here, and would not be in general, is that a
        # page CANNOT PROVOKE AN APPROVAL PROMPT IN THIS ARCHITECTURE. Red tools
        # are selected by the local router from HIS SPEECH; the model never
        # picks a tool (CLAUDE.md invariant 4), so there is no path from page
        # text to a pending request. Every pending approval already originated
        # with him.
        #
        # So the approval is a SECOND human act, on a payload he has read and
        # may have corrected, about an action he asked for. That is precisely
        # the judgement the fence exists to require.
        #
        # WHAT IS DELIBERATELY NOT DONE: `session.owner_approved_red` is never
        # set. That flag is a MODE — it would open every red action for the rest
        # of the session, and one approval would silently disarm the fence for
        # everything after it. The bypass below is scoped to this call and dies
        # with it.
        if self.session is not None:
            try:
                self.session.check_tool(spec.name, spec.tier)
            except InjectionRefusal:
                self._log("APPROVED-OVER-FENCE", spec.name,
                          f"requestId={request_id} approved while external content "
                          f"from {', '.join(getattr(self.session, 'sources', []) or [])} "
                          f"was in context", spec.tier, actor=actor)

        # PASS ONLY THE FLAGS THIS HANDLER ACCEPTS.
        #
        # An adversarial review flagged exactly this and a verifier refuted it
        # as "reachable only from a direct call" — which was true, because the
        # caller did not exist yet. Building the approval path made it
        # reachable, and the first end-to-end run died on
        # `delete() got an unexpected keyword argument '_approved_by_surface'`.
        #
        # `x.post` and `x.reply` take `_approved_by_surface` as defence in depth;
        # `fs.delete`, `shell.execute` and `browser.submit` take `confirmed`.
        # Inspecting the signature rather than maintaining a list means a red
        # tool added later cannot silently break this path.
        import inspect

        accepted = set(inspect.signature(spec.handler).parameters)
        for flag in ("_approved_by_surface", "confirmed"):
            if flag in accepted:
                args[flag] = True
        # WHICH approval this is. A handler that declares `_request_id` gets it,
        # by signature like the flags above, so a queued or deferred action can
        # name the card that authorised it instead of asserting one existed.
        if "_request_id" in accepted:
            args["_request_id"] = request_id
        # ── AUDIT BEFORE EXECUTING, NOT AFTER ────────────────────────────────
        #
        # The pair used to be written after `spec.handler(**args)` returned, so
        # the executed string was recorded ONLY on the success path. An
        # adversarial review found the hole and it is deterministically
        # forceable, not merely unlucky:
        #
        #   he approves `npm install -g X` with an edit appending `& timeout
        #   /t 200`. `shell.execute` runs it as him for the full 120 s, then
        #   `subprocess.run` raises `TimeoutExpired` — which is neither
        #   ToolError nor ToolHold, so it escapes both handlers and the audit
        #   lines never run. The log then holds `APPROVAL FAILED
        #   shell.execute: TimeoutExpired` and, from the earlier
        #   PENDING-APPROVAL entry, the ORIGINAL pre-edit command. The string
        #   that actually ran as him exists nowhere.
        #
        # Worse on `x.post`, which publishes and THEN waits 1200 ms: a
        # Playwright error after publication would lose the text of a tweet that
        # is already public — on the least reversible tool in the build.
        #
        # `core/tools/base.py` already states the principle this violated: the
        # audit template exists "so the log records what was ASKED even when the
        # tool then failed." Recording intent before acting is the only ordering
        # that survives a crash, and CLAUDE.md invariant 7 is about exactly
        # that.
        executed_args = {k: v for k, v in args.items()
                         if k not in ("_approved_by_surface", "confirmed",
                                      *self._FORGEABLE_ARG_KEYS)}
        # A PRIVATE argument (x.send_dm's message, round 4) is withheld from
        # both lines: length + digest in its place, the detail rewritten from
        # the audit line. The card showed him the words; the chain holds that
        # he approved them, to whom, and a digest to check a text against.
        chain_detail, chain_requested = self.chain_view(spec.name, pending.args, pending.detail)
        self._log("REQUESTED", spec.name,
                  f"requestId={request_id} {chain_detail} args={chain_requested}"
                  + (" [external content in context]" if pending.external_at_request else ""),
                  spec.tier, actor=actor)
        self._log("APPROVED" + (" (EDITED)" if was_edited else ""), spec.name,
                  f"requestId={request_id} args={self.chain_args(spec, executed_args)}",
                  spec.tier, actor=actor)

        # `_with_provenance`: the handler's own guard sees the origin the card
        # showed him — `actor`, resolved from `pending.provenance` — not a
        # default. For an agent-built shell.execute that means the handler
        # refuses even after his approval, which is exactly what shell.py
        # promises: "the model asked me and I checked with you" is the shape
        # of a successful injection, and no approval reaches past it.
        handler_args = self._with_provenance(spec, args, actor)
        try:
            result = spec.handler(**handler_args)
        except ToolHold:
            # A red handler that still wants a confirmation has already had one:
            # the approval card IS the confirmation.
            handler_args["confirmed"] = True
            result = spec.handler(**handler_args)
        # ORDER MATTERS AND I GOT IT WRONG ONCE. A bare
        # `except (ToolError, InjectionRefusal): raise` placed ABOVE the
        # ToolError branch shadowed it, so a handler's own refusal escaped as a
        # raw ToolError instead of being converted to ApprovalError — and the
        # test suite crashed rather than failing an assertion. Specific first,
        # catch-all last.
        except ToolError as err:
            # NOT restored. The handler ran and refused on its own terms — X was
            # not signed in, the path was gone. That is an attempted action, and
            # re-offering the card would invite him to approve it again against
            # a world that has not changed.
            #
            # The args are already on the chain, written before the call, so
            # this only has to record the outcome.
            self._log("APPROVED-BUT-FAILED", spec.name,
                      f"requestId={request_id} {err.reason}", spec.tier, actor=actor)
            raise ApprovalError("internal", err.reason) from None
        except InjectionRefusal:
            raise
        except Exception as exc:  # noqa: BLE001
            # ANYTHING ELSE — a subprocess timeout, a Playwright error, a driver
            # crash. The action may have half-happened or fully happened; the
            # `x.post` case publishes and only THEN waits. Recorded against args
            # that are already on the chain, then re-raised.
            self._log("APPROVED-THEN-CRASHED", spec.name,
                      f"requestId={request_id} {type(exc).__name__}: {exc}",
                      spec.tier, actor=actor)
            raise

        self._absorb_external(spec, result, actor=actor)

        spoken = spec.success.format(**result) if isinstance(result, dict) else ""
        return {"tool": spec.name, "tier": spec.tier, "requestId": request_id,
                "requested_args": dict(pending.args),
                "executed_args": executed_args,
                "edited": was_edited, "spoken": spoken, "result": result}

    # ── the red gate and the fence ───────────────────────────────────────────

    # ── PROVENANCE IS NEVER READ FROM ARGS ──────────────────────────────────
    #
    # The approval card used to take its provenance from
    # `args.get("provenance", "human")`. Every call is router-built from his
    # own speech today, so that was harmless — and it is a hole the moment a
    # model builds a call: nothing stops the model writing
    # `args["provenance"] = "human"` on its own tool pick, and the card, which
    # is THE security boundary for every red action, would then say the owner
    # asked for it. The card's answer to "who wants this" must come from the
    # call's `origin` field, set by whatever constructed the call, and the keys
    # that could impersonate it are removed from `args` before anything reads
    # them — the same way `_approved_by_surface` is made unforgeable.
    #
    # THE EXECUTION FLAGS ARE THE SAME CLASS — AND A BYPASS, NOT A MISLABEL.
    # The red gate used to read `args.get("_approved_by_surface")` and the hold
    # read `args.get("confirmed")`, and neither key was stripped here. So a
    # model-built call carrying `_approved_by_surface: true` would have walked
    # past the approval card outright, and one carrying `confirmed: true` would
    # have skipped the hold. Both are stripped at this same point now, and
    # neither gate reads `args` any more: a red tool in `_dispatch_registry`
    # ALWAYS raises a request (the only execution route is `execute_approved`,
    # which puts the flag on the handler's args by signature AFTER this strip),
    # and a hold is satisfied only by `ToolCall.confirmed` — a field set by
    # `answer_confirmation` once the ledger accepted his "yes" — or by the
    # ledger's own repeat match. A model's args cannot reach either.
    #
    # ONLY these four keys. `_ctx` belongs to the vault's ActionContext, which
    # does not exist yet; it is stripped when that lands, not here.
    _FORGEABLE_ARG_KEYS = ("provenance", "origin", "_approved_by_surface", "confirmed")

    @staticmethod
    def _actor_of(origin: Any) -> str:
        """
        The card's provenance for a call, from its `origin` field.

        Missing or unrecognised collapses to `schedule` — the most restrictive
        actor in core/security/guard.py — because a builder that forgot to say
        who it is must not be treated as the owner by default.
        """
        return origin if origin in ("human", "agent", "schedule") else "schedule"

    def _tier_of(self, name: str) -> str:
        """
        The tier permissions.yaml assigns to a tool, for an entry written with
        no `spec` in scope — a refusal caught in `run`, a cancelled hold.

        Registry first, then the legacy map, and an unknown name is `red`: the
        same policy `_dispatch` applies when it RUNS one. Without this those
        entries took `_log`'s default and said `green` for red and amber
        tools, so the chain understated the severity of exactly the actions
        that were stopped.
        """
        spec = REGISTRY.get(name)
        if spec is not None:
            return spec.tier
        return self._LEGACY_TIERS.get(name, "red")

    @staticmethod
    def _with_provenance(spec: Any, args: dict[str, Any], origin: str) -> dict[str, Any]:
        """
        The args to CALL a handler with: `args`, plus `provenance=origin` when
        the handler declares that parameter.

        THIS IS WHAT MAKES shell.execute's OWN GUARD REAL AGAIN. P6 strips
        `provenance` out of `args` so a builder cannot forge it — correct — but
        nothing put the RESOLVED value back, so `shell.execute` only ever saw
        its default "human" and its `provenance != "human"` refusal was dead
        code. The handler now receives the same origin the card and the chain
        were given: the call's field, never a key from args.

        By signature, like `_approved_by_surface` and `confirmed` on the
        approval path, so a handler that does not take it is never handed an
        unexpected keyword. Returns a COPY: `args` itself stays as it arrived
        (stripped), because the hold, the audit line and the repeat comparison
        all read it after the call and must not see an injected key.
        """
        import inspect

        out = dict(args)
        try:
            params = inspect.signature(spec.handler).parameters
        except (TypeError, ValueError):
            return out
        if "provenance" in params:
            out["provenance"] = origin
        return out

    def _strip_forgeable(self, tool: str, args: dict[str, Any], *, actor: str,
                         tier: str) -> None:
        """
        Remove impersonation keys from `args`, in place, and say so.

        `actor` is the call's REAL origin, already resolved by the caller. It
        is required here precisely because this entry records an attempt to
        forge provenance: the one thing it must not do is record that attempt
        under the forged identity, or under a default.

        `tier` is the tool's real tier, required for the same reason: this
        entry used to take `_log`'s default and say `green` for a forged key
        on `shell.execute`, understating the one attempt the chain most needs
        to show at full severity.
        """
        forged = [k for k in self._FORGEABLE_ARG_KEYS if k in args]
        for k in forged:
            args.pop(k, None)
        if forged:
            self._log("STRIPPED-FORGEABLE", tool,
                      f"{', '.join(forged)} arrived in args and were discarded — "
                      f"provenance comes from the call's origin, approval from "
                      f"the card, confirmation from the ledger; never from args",
                      tier, actor=actor)

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

        # ONE MAPPING, ARGS LAST — NOT `_Missing(name=spec.name, **args)`.
        #
        # That form is a TypeError the moment a tool has an argument called
        # `name`: "got multiple values for keyword argument 'name'". The bare
        # `except` below then swallowed it and returned the bare tool name, so
        # the chain recorded `ran proc.find` instead of `ran find process
        # chrome` — and the same for `win.close`, `win.focus`, `fs.search` and
        # `browser.click`. Five tools whose audit template asks WHAT was acted
        # on, and the answer was silently dropped on every single call.
        #
        # Found 2026-09-07 by the batch-2 observer proof, which asserted that
        # a named lookup appears on the chain and discovered it never had.
        # This is the line `core/tools/base.py` describes as existing "so the
        # log records what was ASKED even when the tool then failed", and it
        # had not been doing that for any tool with a `name` parameter.
        #
        # `spec.name` stays as the FALLBACK for `ToolSpec.audit`'s default
        # template, `"{name}"`, which renders the tool's own name when the
        # call carries no `name` argument. Args override it, which is the
        # right precedence: the template asks about the target, and the
        # target is in the args.
        try:
            return spec.audit.format_map(_Missing({"name": spec.name, **Executor.chain_args(spec, args)}))
        except Exception:  # noqa: BLE001
            return spec.name

    # ── the chain view of PRIVATE arguments (X direct messages, round 4) ────
    #
    # `x.send_dm` declares `private_args=("text",)`: the message he dictated
    # is shown on the CARD (that is where he reads what he approves) and is
    # WITHHELD from every chain line — length + sha256 digest in its place —
    # because a DM is private by definition and the chain is read by more
    # eyes than the card. Nothing here decides anything: the card, the
    # freeze, the flag-strip and the fence are untouched; only what the log
    # SAYS about a private argument changes.

    @staticmethod
    def _private_args_of(name: str) -> tuple[str, ...]:
        spec = REGISTRY.get(name)
        return tuple(getattr(spec, "private_args", ()) or ()) if spec is not None else ()

    @staticmethod
    def chain_args(spec: Any, args: dict[str, Any]) -> dict[str, Any]:
        """
        `args` as the chain may hold them: a copy, with every `spec.private_args`
        value replaced by `<withheld: N chars, sha256 xxxxxxxxxxxxxxxx>`.
        Identity for every tool without private args.
        """
        private = tuple(getattr(spec, "private_args", ()) or ())
        out = dict(args)
        for key in private:
            if key in out and out[key] not in (None, ""):
                s = str(out[key])
                out[key] = (f"<withheld: {len(s)} chars, sha256 "
                            f"{hashlib.sha256(s.encode('utf-8')).hexdigest()[:16]}>")
        return out

    def chain_view(self, tool: str, args: dict[str, Any], detail: str) -> tuple[str, dict[str, Any]]:
        """
        (detail, args) for a chain entry about `tool`. Unchanged for every
        tool without private args. For one WITH them the detail — the card's
        spoken line, which names the words — is replaced by the audit line
        (which withholds them) and the args by `chain_args`.
        """
        spec = REGISTRY.get(tool)
        if spec is None or not tuple(getattr(spec, "private_args", ()) or ()):
            return detail, dict(args)
        return self._audit_line(spec, args), self.chain_args(spec, args)

    def is_private(self, name: str) -> bool:
        """True for a tool whose turn text must be withheld from the chain and the log."""
        spec = REGISTRY.get(name)
        return bool(spec is not None and (getattr(spec, "private", False)
                                          or tuple(getattr(spec, "private_args", ()) or ())))

    @staticmethod
    def _red_detail(spec: Any, args: dict[str, Any]) -> str:
        """
        What she names back to him. The SPECIFIC target, never the category —
        "delete C:\\Users\\...\\old" and not "a delete", because the whole point
        of an approval is that he can see what he is approving.

        A core/system capability may DESCRIBE its target (software-change
        round, 2026-09-12): "install VLC media player (VideoLAN.VLC) from the
        winget catalog" names the exact package id, which none of the generic
        keys below would. `describe` may raise ToolError to REFUSE before the
        card — the red gate catches that; any other failure falls back to the
        generic line, because a card must never fail to be raised over
        wording. A spec without the hook is untouched.
        """
        cap = getattr(getattr(spec, "handler", None), "__wrapped_capability__", None)
        # A hand-written ToolSpec may carry the same hook (round 3: x.quote
        # names the quoted post and its author; x.thread its length and first
        # line). Same contract: ToolError refuses, anything else falls back.
        describe = getattr(cap, "describe", None) or getattr(spec, "describe", None)
        if describe is not None:
            try:
                return str(describe(dict(args)))[:200]
            except ToolError:
                raise
            except Exception:  # noqa: BLE001
                pass
        for key in ("path", "command", "text", "url", "name"):
            if key in args and args[key]:
                return f"{spec.name} on {str(args[key])[:120]}"
        return spec.name

    def _absorb_external(self, spec: Any, result: dict[str, Any],
                         actor: str = "system") -> None:
        """
        Load a tool's outside-world output into the fence.

        `actor` is who initiated the tool whose output this is — the dispatch
        and approval paths pass the call's resolved origin. The default is
        the ONE out-of-context case in this file: the fence being loaded with
        no call in flight (core/tests/test_injection.py does this directly).
        That is the daemon's own act, so it is `system` — never "human", which
        would put the owner's name on a page he did not ask for.

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
                      spec.tier, actor=actor)
            self.last_injection = {"source": source, "patterns": fired}

    def _note_claims(self, spec: Any, result: dict[str, Any], *, actor: str) -> None:
        """
        Hand an X read's structured posts to the claim store — the ones about
        HIS topics.

        ONLY `x.read_*`, ONLY `result["posts"]`, and ONLY into a store that
        can write nothing but UNVERIFIED (core/brain/claims.py). Runs AFTER
        `_absorb_external`, so the fence is already up when the posts are
        noted; nothing in them can reach an amber or red tool this turn, and
        nothing in them can become a belief in any turn.

        THE SELECTIVE FILTER (core/brain/relevance.py) stands in front of the
        store and decides note-or-skip, nothing else. It is handed each post's
        TEXT and nothing else, it answers a bool, and a post it passes goes
        through the same `note_read` as before, so a kept claim is exactly as
        UNVERIFIED as a Stage 1 claim, and a post cannot vote itself in by
        claiming to be relevant or trusted: the score is computed from HIS
        words, not the post's. With no filter supplied (a test, or
        `claims.filter.enabled: false`) every post is noted: Stage 1,
        unchanged. A filter that raises drops the post, the safe direction.

        The audit entry carries the CONTENT's provenance, `external`, on the
        chain's own provenance field, and names the kept claims by id and
        handle, never by text, so a post carrying a secret-shaped string is
        not copied onto the chain. Skipped posts are a COUNT: not stored, not
        named, not quoted. A read that kept nothing still leaves a
        SKIPPED-CLAIMS line, so a quiet store is explained by the chain.
        """
        store = getattr(self, "claims", None)
        if store is None or not isinstance(result, dict):
            return
        # A PRIVATE read (x.read_dm, round 4, 2026-09-12) is never learning
        # material: his inbox is not strangers' public text. Excluded on the
        # spec's flag BEFORE the prefix test — "x.read_dm" starts with
        # "x.read", and a name test alone would have carried it in — and its
        # result carries `messages`, never `posts`, so the store could not
        # see them by that route either.
        if getattr(spec, "private", False):
            return
        if not str(spec.name).startswith(_X_READ_PREFIXES):
            return
        posts = result.get("posts")
        if not isinstance(posts, list) or not posts:
            return
        source = str(result.get("external_source") or spec.name)
        gate = getattr(self, "relevance", None)
        skipped = errors = 0
        if gate is not None:
            kept: list[Any] = []
            for p in posts:
                try:
                    text = p.get("text") if isinstance(p, dict) else ""
                    ok = bool(gate.keep(str(text or "")))
                except Exception:  # noqa: BLE001 — a broken filter keeps nothing
                    ok = False
                    errors += 1
                if ok:
                    kept.append(p)
                else:
                    skipped += 1
            posts = kept
        noted: list[Any] = []
        if posts:
            try:
                noted = store.note_read(posts, source=source)
            except Exception:  # noqa: BLE001 — learning must never break a turn
                noted = []
        if self._audit is None or (not noted and not skipped):
            return
        detail: dict[str, Any] = {
            "claims": [c.id for c in noted], "provenance": "external-untrusted",
            "status": "unverified",
            "filter": "topics-overlap" if gate is not None else "off",
            "skipped": skipped}
        if errors:
            detail["filter_errors"] = errors
        if noted:
            who = ", ".join(f"{c.who}/{c.source.post_id or '-'}" for c in noted[:5])
            tried = sum(1 for c in noted if c.injection)
            summary = (f"NOTED-CLAIMS {len(noted)} unverified claim(s) from {source}: {who}"
                       + (f" ({tried} carried injection patterns)" if tried else "")
                       + (f"; skipped {skipped} not about his topics" if skipped else ""))
        else:
            known = True
            try:
                known = bool(gate.has_topics()) if gate is not None else True
            except Exception:  # noqa: BLE001
                pass
            summary = (f"SKIPPED-CLAIMS {skipped} post(s) from {source}: "
                       + ("no topics known" if not known else "none about his topics")
                       + ", nothing noted")
        try:
            self._audit.append(
                actor=actor if actor in self._AUDIT_ACTORS else "schedule",
                tool=spec.name, tier=spec.tier, provenance="external",
                summary=summary, detail=detail)
        except Exception:  # noqa: BLE001
            pass

    def _absorb_style(self, spec: Any, result: dict[str, Any], *, actor: str) -> None:
        """
        Hand an X read's posts to the style store — HOW they were written.

        ONLY `x.read_*`, ONLY `result["posts"]`, ONLY into a store whose
        every field is a number or a single word (core/brain/style.py). Runs
        after `_absorb_external` (the fence is up) and after `_note_claims`
        (the content half is done); touches neither the session nor the
        claim store nor the thread. A toxic or instruction-shaped post
        contributes its shape and no words.

        The chain entry carries counts only — never a word from a post — under
        the content's provenance, `external`.
        """
        store = getattr(self, "style", None)
        if store is None or not isinstance(result, dict):
            return
        # PRIVATE (x.read_dm): not even its shape. See `_note_claims`.
        if getattr(spec, "private", False):
            return
        if not str(spec.name).startswith(_X_READ_PREFIXES):
            return
        posts = result.get("posts")
        if not isinstance(posts, list) or not posts:
            return
        source = str(result.get("external_source") or spec.name)
        try:
            got = store.absorb_read(posts, source=source)
        except Exception:  # noqa: BLE001 — learning must never break a turn
            return
        if not got.n or self._audit is None:
            return
        withheld = []
        if got.toxic:
            withheld.append(f"{got.toxic} toxic")
        if got.instruction:
            withheld.append(f"{got.instruction} instruction-shaped")
        try:
            self._audit.append(
                actor=actor if actor in self._AUDIT_ACTORS else "schedule",
                tool=spec.name, tier=spec.tier, provenance="external",
                summary=(f"ABSORBED-STYLE {got.n} post(s) from {source}: "
                         f"{got.owner} his, {got.others} others; patterns only"
                         + (f"; words withheld from {', '.join(withheld)}" if withheld else "")),
                detail={"provenance": "external-untrusted", "holds": "patterns-only",
                        "n": got.n, "owner": got.owner, "others": got.others,
                        "toxic_withheld": got.toxic,
                        "instruction_withheld": got.instruction})
        except Exception:  # noqa: BLE001
            pass

    # ── audit ────────────────────────────────────────────────────────────────

    #: CONTRACT §6.2 Provenance, as core/security/audit.py spells it. Imported,
    #: not copied: core/tests/test_contract_sync.py holds audit.py to the enum.
    _AUDIT_ACTORS = frozenset(get_args(_AuditActor))

    def _log(self, verb: str, tool: str, summary: str, tier: str = "green",
             *, actor: str) -> None:
        """
        One chain entry, attributed to whoever INITIATED the action.

        THIS USED TO WRITE actor="human" UNCONDITIONALLY. P6 made the approval
        card report the call's real origin, so a model-built red action showed
        `agent` on the card — and `human` on the hash-chained audit log, the
        tamper-evident record that exists to answer exactly that question. The
        card and the chain disagreed, and the chain was the one lying.

        `actor` is now REQUIRED and keyword-only, with no default — the rule
        `AuditLog.append` already enforces one layer down. Every call site
        passes the SAME resolved value the card is given: `_actor_of(call.
        origin)` at dispatch, or `pending.provenance`, which is that value
        stored on the request. So the two cannot diverge. A site that forgets
        to say who is a TypeError at the call, not a silent misattribution on
        the chain. Entries with no call in flight (daemon start, auth, PTY
        lifecycle) are written by server.py and already say `system`.

        VALUE-ONLY, BY CONSTRUCTION. `actor` was already one of the nine fields
        `AuditLog.append` hashes, in a shape every existing entry shares. This
        changes what the field SAYS, never what is hashed or in what order:
        old entries still verify, and a new entry chains onto an old one
        unchanged. Proven on a copy of the live chain before shipping.

        Anything outside CONTRACT §6.2's six values collapses to `schedule`,
        the same policy as `_actor_of`: an actor nobody can name is not the
        owner.
        """
        if self._audit is None:
            return
        if not isinstance(actor, str) or actor not in self._AUDIT_ACTORS:
            actor = "schedule"
        try:
            self._audit.append(actor=actor, tool=tool, tier=tier,
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

        # 0a. IMPERSONATION KEYS OUT, BEFORE ANY READ. See `_FORGEABLE_ARG_KEYS`.
        #     `origin` is the call's own field, resolved once here and threaded
        #     to the card, the hold, and the audit — never re-read from args.
        #     Resolved BEFORE the strip so the strip's own entry is attributed
        #     to the real origin (the field on the call, untouched by args).
        origin = self._actor_of(call.origin)
        self._strip_forgeable(spec.name, args, actor=origin, tier=spec.tier)

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
                      f"{had} source(s) dropped: {', '.join(srcs) or 'none'}", "green",
                      actor=origin)
            return spec.success

        # 1. THE FENCE. Raises InjectionRefusal, caught in run().
        if self.session is not None:
            self.session.check_tool(spec.name, spec.tier)

        # 1b. THE VOICEPRINT GATE — a BORDERLINE voice may open a folder and may
        #     not approve a tweet.
        #
        #     `voice_verdict` is set by the voice loop when a segment was scored
        #     and PASSED. A verdict that could not be formed at all (unenrolled,
        #     no model, too short) is never set here, so this gate is silent in
        #     exactly the fail-open cases and active only when there is a real
        #     number to act on.
        #
        #     Green is untouched: the whole design errs toward accepting HIM,
        #     and everything that could actually hurt him is red and needs the
        #     approval card regardless of who spoke.
        verdict = getattr(self, "voice_verdict", None)
        if verdict is not None and spec.tier in ("amber", "red"):
            confident = getattr(self, "voice_confident", 0.62)
            if not verdict.allows(spec.tier, confident):
                self._log("REFUSED-VOICEPRINT", spec.name,
                          f"score {verdict.score:.3f} below the confident "
                          f"threshold for {spec.tier}", spec.tier, actor=origin)
                return (f"I am not certain enough that is you, Emperor. "
                        f"Use the card for that one.")

        # 2. THE RED GATE. A red tool NEVER executes from a voice turn, however
        #    many times he says yes. It raises a permission request and stops.
        #    See core/brain/approvals.py for why voice is not an approval
        #    surface, and for what this changed about fs.delete and
        #    shell.execute, which used to execute on a spoken confirmation.
        #
        #    NO ARGS READ. This used to be `and not args.get("_approved_by_
        #    surface")`, and that was the bypass: the key was never stripped,
        #    so a model-built call carrying it walked past the card. A red tool
        #    reaching this function ALWAYS raises a request. The one execution
        #    route is `execute_approved`, which is not a dispatch.
        if spec.tier == "red":
            try:
                # 2a. RESOLVE THE TARGET BEFORE THE CARD (ToolSpec.resolve —
                #     X publishing round 3, 2026-09-12). The card is built
                #     from these args and the handler is not reached before
                #     it, so this is the one place "quote post two" can become
                #     the frozen status id, the quoted post's text and its
                #     injection count that the card must SHOW him. Runs ONCE,
                #     on a copy, after the strip and the fence; a ToolError
                #     here is the same refusal-before-the-card as a describe's.
                #     The resolved args are what the request is raised on and
                #     what `execute_approved` strips again before running.
                resolve = getattr(spec, "resolve", None)
                if resolve is not None:
                    args = dict(resolve(dict(args)))
                detail = self._red_detail(spec, args)
            except ToolError as err:
                # A core/system capability's `describe` REFUSED THE TARGET
                # BEFORE THE CARD (capability.py: "refuse a catastrophic target
                # before he is ever asked"). Software-change round, 2026-09-12:
                # "uninstall Python" would take this daemon down, and a file
                # path where a catalog id should be is not a package — neither
                # becomes a card he could approve at 2am. Audited REFUSED under
                # the call's true origin and spoken as the tool's own failure
                # line, exactly like a post-approval ToolError. Nothing here
                # reads args for a flag; nothing here runs anything.
                self._log("REFUSED", spec.name, f"before the card: {err.reason}", "red",
                          actor=origin)
                reason = err.reason.rstrip(". ")
                reason = (reason[:1].upper() + reason[1:] + ".") if reason else "."
                return spec.failure.format(reason=reason, alternative=err.alternative)
            # `origin`, NOT `args.get("provenance", "human")`. That read was the
            # spoofable half of P6: the card must report who BUILT the call.
            req = self.approvals.request(
                tool=spec.name, args=args, tier="red",
                provenance=origin, detail=detail,
                # WHICH ARGUMENTS THE CARD MAY NOT EDIT, from the tool itself.
                # `x.reply` freezes its target so an edited approval cannot
                # land approved words under a different post. `getattr` with a
                # default because a ToolSpec built before this field existed
                # (a test's `dataclasses.replace`, a hand-made spec) must keep
                # working with nothing frozen.
                frozen=tuple(getattr(spec, "frozen", ()) or ()),
            )
            req.external_at_request = bool(
                getattr(self.session, "external_content_in_context", 0))
            self._log("PENDING-APPROVAL", spec.name,
                      f"requestId={req.request_id} {self._audit_line(spec, args)}",
                      "red", actor=origin)
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
            dropped = self.ledger.pending
            if dropped is not None:
                # READ ONCE. `pending` is a property that turns None the moment
                # the hold expires; reading it again for `.tool` left a window
                # where the second read could be None. Attributed to whoever
                # asked for the HELD action — the entry names that tool, so
                # that is the initiator it answers for.
                self._log("HOLD-DROPPED", dropped.tool,
                          "a red refusal made a pending 'yes' ambiguous", "amber",
                          actor=self._actor_of(getattr(dropped, "origin", None)))
                self.ledger.clear()
            return red_refusal(spec.name, detail)

        # 3a. AN AMBER ACTION THE MODEL BUILT ASKS FIRST (intent round,
        #     2026-09-22). core/security/guard.py has said so since it was
        #     written — amber is ALLOW for the owner and CONFIRM for any other
        #     actor — but this path only ever consulted `spec.holds`, because
        #     until this round the only builder was the router, stamping
        #     "human". Now the brain builds calls under origin="agent": a move,
        #     a rename, a click it resolved from his sentence runs only after
        #     his "yes", exactly as a holding tool does. Armed on the args as
        #     they arrived, same origin, so his repeat or his "yes" re-runs THIS
        #     call (`answer_confirmation` carries the origin) and nothing else.
        #     Green is untouched; red is the card above; a human-built amber
        #     call is exactly as it was; a holding amber tool still holds
        #     through its own handler below.
        if (spec.tier == "amber" and origin != "human" and not spec.holds
                and not bool(getattr(call, "confirmed", False))
                and self.ledger.resolve_repeat(spec.name, args, origin=origin) is None):
            detail = f"{self._audit_line(spec, args)}, which the model picked from your words"
            self.ledger.arm(spec.name, args, detail, origin=origin)
            self._log("HELD", spec.name, f"{self._audit_line(spec, args)} (model-built amber)",
                      spec.tier, actor=origin)
            return destructive_hold(detail)

        # 3. THE HOLD — AMBER ONLY now. A repeat of the same command IS the
        #    confirmation she promised out loud (core/brain/confirm.py), and it
        #    is still the right control for `proc.kill`: he named a PID, the act
        #    is instant, and nothing leaves the machine.
        #
        #    NO ARGS READ HERE EITHER. `confirmed` is stripped above; what
        #    satisfies a hold is `call.confirmed` — the FIELD on the call, set
        #    only by `answer_confirmation` after the ledger accepted his "yes"
        #    — or the ledger's own repeat match. The flag is then put on the
        #    handler's args BY THE EXECUTOR, after the strip, so the handler
        #    sees what it always did and a model's args never reach it.
        if spec.holds:
            # `origin=origin`: a repeat only confirms a hold armed by the SAME
            # origin. An agent-built twin of his held command is not his "yes".
            if (bool(getattr(call, "confirmed", False))
                    or self.ledger.resolve_repeat(spec.name, args, origin=origin) is not None):
                args["confirmed"] = True
            # else: fall through, the handler raises ToolHold and we arm below.

        try:
            # `_with_provenance`: a handler that declares `provenance` gets the
            # RESOLVED origin — the value the card and the chain carry.
            result = spec.handler(**self._with_provenance(spec, args, origin))
        except ToolHold as hold:
            # 3b. THE HANDLER MAY RESOLVE THE TARGET IT HOLDS ON (X engagement
            #     round, 2026-09-12). "like post two" arrives as {index: 2};
            #     x_tools resolves the ordinal against the snapshot of the last
            #     read ONCE and raises ToolHold(resolved={post_id}). The hold is
            #     armed on THAT — the status id — so his "yes" re-runs the id
            #     and never re-reads a position: a green read between the hold
            #     and the yes would otherwise move "post two" onto a different
            #     person's post. A hold with no resolved args is armed on the
            #     args as they arrived, exactly as before.
            #
            #     A REPEAT THAT RESOLVES TO THE SAME TARGET IS THE REPEAT SHE
            #     PROMISED. The ledger compared the RAW args above and they
            #     differ from the held ones by construction ({index} vs
            #     {post_id}), so the match is made here on the resolved args,
            #     same origin, and the call is re-dispatched with `confirmed`
            #     on the FIELD — through this same function, so the strip, the
            #     fence and the voiceprint gate all run again. A repeat that
            #     resolves to a DIFFERENT target (the timeline moved) matches
            #     nothing and replaces the hold: one hold at a time, and she
            #     names the new target before anything happens.
            resolved = getattr(hold, "resolved", None)
            held_args = dict(resolved) if resolved else args
            if resolved and self.ledger.resolve_repeat(spec.name, held_args, origin=origin) is not None:
                return self._dispatch_registry(ToolCall(
                    name=spec.name, args=held_args, tier=call.tier, speech=call.speech,
                    origin=call.origin, confirmed=True))
            # The hold remembers who asked, so his "yes" re-runs it AS THAT
            # ORIGIN — a model-initiated amber action confirmed by voice must
            # not come back through `answer_confirmation` re-labelled "human".
            self.ledger.arm(spec.name, held_args, hold.detail, origin=origin)
            self._log("HELD", spec.name, self._audit_line(spec, held_args), spec.tier,
                      actor=origin)
            return destructive_hold(hold.detail)
        except ToolError as err:
            self._log("FAILED", spec.name, f"{err.reason}", spec.tier, actor=origin)
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
        self._absorb_external(spec, result, actor=origin)

        # 5b. LEARN — AFTER THE FENCE, NEVER INSTEAD OF IT. An X read's posts
        #     become UNVERIFIED CLAIMS in their own store (core/brain/claims.py).
        #     The fence above is already up, so nothing in those posts can
        #     reach an amber or red tool this turn; and the store cannot be
        #     told a status, so nothing in them becomes a belief in any turn.
        #     Only the posts about HIS topics are kept (core/brain/relevance.py)
        #     — a note-or-skip gate that changes nothing about their status.
        self._note_claims(spec, result, actor=origin)

        # 5c. LEARN THE FORM, ALSO AFTER THE FENCE. The same posts teach the
        #     style store how people write — numbers and single words, never a
        #     sentence (core/brain/style.py) — so she sounds more natural as
        #     she reads more, and cannot learn a fact from it.
        self._absorb_style(spec, result, actor=origin)

        self._log("ran", spec.name, self._audit_line(spec, args), spec.tier,
                  actor=origin)
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
                      f"handler returned {sorted(result)}", spec.tier, actor=origin)
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
        self._log("ran(legacy)", name, f"{name} {args}", tier,
                  actor=self._actor_of(call.origin))

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
            # RE-RESOLVED HERE RATHER THAN CARRIED IN THE ARGS.
            #
            # The router already picked this entry, and it could have passed the
            # launch path along — but then a path would ride in a tool argument
            # that is audited and, once evt.agent.state.detail ships, broadcast.
            # The key is enough: `resolve` exact-matches it and dedupes, so the
            # answer is the same one the router chose, derived in Python at the
            # moment of execution. CLAUDE.md invariant 4 in spirit as well as
            # letter — the model supplies a name, Python supplies the command.
            from .appindex import get_index, launch
            entries, _how = get_index().resolve(app)
            if not entries:
                return action_failed(f"I cannot find {app}", "Say the name again?")
            ok, detail = launch(entries[0])
            if not ok:
                return action_failed(f"{app} would not start ({detail})",
                                     "Try it again, or name it differently.")
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
            # confirmation is the caller's to obtain (tessa.md: she says it and
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
