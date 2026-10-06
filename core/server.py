"""
core/server.py — Tessa Core daemon: the local WebSocket server.

Implements CONTRACT.md §1-§5. This is the process both surfaces talk to.

The security posture here is the reason this file exists before any UI code.
Loopback is NOT a security boundary: every webpage the owner visits can run
`new WebSocket('ws://127.0.0.1:47600/v1')`. Three controls stop that, and all
three must hold (CONTRACT §2.1):

    1. Origin allowlist  — browsers always send Origin and cannot forge it
    2. Per-launch token  — read from a user-only-ACL file
    3. Handshake deadline — 3s, then the socket closes

Run:  python core/server.py --dev
"""

from __future__ import annotations

# ── THE CONSOLE-CLOSE ABORT, SWITCHED OFF BEFORE ANYTHING ELSE LOADS ─────────
#
# Whisper's ctranslate2 ships Intel's OpenMP runtime (libiomp5md.dll), and that
# DLL installs its own console handler which answers CTRL_C / CTRL_CLOSE with
# `forrtl: error (200): program aborting due to window-CLOSE event` — ending
# the process before the daemon's clean shutdown can run. Every daemon from
# 23 Sep to 5 Oct died that way. The runtime reads this variable when the DLL
# loads, so it is set HERE, ahead of every other import; only the docstring and
# `from __future__` may come first. scripts/start-tessa.cmd sets it as well —
# this line is what protects a daemon started any other way.
import os

_FOR_DISABLE_INHERITED = os.environ.get("FOR_DISABLE_CONSOLE_CTRL_HANDLER")
os.environ["FOR_DISABLE_CONSOLE_CTRL_HANDLER"] = "1"

import argparse  # noqa: E402
import asyncio  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Iterable  # noqa: E402

import websockets  # noqa: E402
import yaml  # noqa: E402
from websockets.asyncio.server import ServerConnection, serve  # noqa: E402
from websockets.http11 import Request, Response  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
# Generated from packages/protocol/schema/enums.json — the single source of
# truth shared with TypeScript. Never hand-maintain a second copy here.
sys.path.insert(0, str(ROOT_DIR / "packages" / "protocol" / "gen" / "python"))

from tessa_protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    PTY_REPORT_EVENTS,
    SURFACES,
    CLOSE_UNAUTHORIZED,
    CLOSE_HANDSHAKE_TIMEOUT,
    CLOSE_PROTOCOL_MISMATCH,
    CLOSE_RATE_LIMITED,
)
from core.brain.approvals import (APPROVAL_WINDOW_S, ApprovalError,  # noqa: E402
                                  iso_in as _iso_in)
from core.brain.claims import ClaimStore  # noqa: E402
from core.brain.claims import bind as bind_claims  # noqa: E402
from core.brain.conversation import Conversation  # noqa: E402
from core.brain.intents import is_private_utterance  # noqa: E402
from core.brain.relevance import from_settings as relevance_from_settings  # noqa: E402
from core.brain.style import StyleStore  # noqa: E402
from core.brain.style import bind as bind_style  # noqa: E402
from core.brain.style import enabled as style_enabled  # noqa: E402
from core.brain.style import resolve_owner_handle  # noqa: E402
from core.brain.llm import describe_engines, make_engine  # noqa: E402
from core.tools.browser import close_browser as close_browser_on_shutdown  # noqa: E402
from core.tools.browser import reap_orphan  # noqa: E402
from core.brain.persona import loaded as persona_loaded  # noqa: E402
from core.brain.persona import system_prompt as persona_system_prompt  # noqa: E402
from core.brain.provenance import SessionContext  # noqa: E402
from core.security.audit import AuditLog  # noqa: E402
from core.security.identity import IdentityError, assert_not_service_account  # noqa: E402
from core.security.guard import Guard, Verdict  # noqa: E402
from core.security import runtime as rt  # noqa: E402
from core import lifecycle  # noqa: E402
from core.pty.grants import GrantRegistry, RedeemResult, Session  # noqa: E402
from core.telemetry.cost import CURRENCY, CostLedger  # noqa: E402
from core.telemetry.health import HealthCollector, HealthConfig  # noqa: E402
# The credential vault (core/capabilities/vault — imported here, never importing
# back; VAULT-DESIGN.md §0.3). `vault.tools` is the one vault module that touches
# core.tools and is imported HERE, not from the vault package's __init__.
from core.capabilities.vault import Vault as _Vault, VaultWs as _VaultWs  # noqa: E402
from core.capabilities.vault.tools import specs as _vault_specs  # noqa: E402
from core.tools import REGISTRY as _REGISTRY  # noqa: E402

# ── constants from CONTRACT ───────────────────────────────────────────────────

DAEMON_VERSION = "0.1.0"

#: CONTRACT §4.1 keys `evt.agent.state`, `evt.transcript.*` and the companion
#: roster by `companionId`. One companion exists today; naming it explicitly now
#: is what stops the field being invented later, per-call and inconsistently.
DEFAULT_COMPANION_ID = "tessa"
PREFERRED_PORT = 47600
PORT_SCAN_LIMIT = 20

# How long to wait for a `killed` report before auditing the revoke as
# unsatisfied. Derived from the Console's kill ladder, not picked round:
#
#   rung 1 host shutdown       1500 ms  (deadline)
#   rung 2 taskkill /F /T      1000 ms  (deadline)
#   rung 3 settle + re-poll     700 ms  (deadline)
#                             -------
#   ladder worst case          3200 ms
#   + tasklist cross-check at up to 3 decision points, measured 283-509 ms each
#                            ~+1500 ms
#   + the report round trip (grant p95 63 ms)      negligible
#                             -------
#   worst realistic           ~4800 ms      observed end-to-end: 3905 ms
#
# 10 s is ~2x the worst realistic case, so it will not fire on a slow-but-
# compliant revoke, and it bounds an unrecorded non-compliance to ten seconds.
REVOKE_CONFIRM_TIMEOUT_S = 10.0

# How long a voice turn may take before it is audited as STALLED.
#
# Derived, not picked round: Gerald's worst OBSERVED sttMs was 29400 ms, and the
# worst full turn (STT + TTS + playback start) was 29672 ms. 60 s is ~2x that,
# so it cannot fire on a slow-but-working turn on this machine.
#
# It does NOT cancel the turn — a thread cannot be cancelled and pretending
# otherwise would leave a half-run tool. It records WHERE the turn got to and
# releases the sphere, so a hang is legible in one minute instead of twelve.
VOICE_TURN_STALL_S = 60.0
WS_PATH = "/v1"
ALLOWED_ORIGINS = frozenset({"tessa://console", "tessa://orb"})
HANDSHAKE_DEADLINE_S = 3.0
MAX_FRAME_BYTES = 1024 * 1024

FAILURE_WINDOW_S = 60.0
FAILURE_LIMIT = 5

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
TYPE_RE = re.compile(r"^(cmd|res|err|evt)\.[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)*$")

ROOT = Path(__file__).resolve().parent.parent

KNOWN_COMMANDS = frozenset({
    "cmd.hello", "cmd.subscribe", "cmd.unsubscribe", "cmd.agent.message",
    "cmd.agent.cancel", "cmd.companion.switch", "cmd.job.create", "cmd.job.cancel",
    "cmd.job.retry", "cmd.permission.respond", "cmd.config.get", "cmd.config.set",
    "cmd.audit.query", "cmd.ping",
    "cmd.pty.requestSpawn", "cmd.pty.report",
    "cmd.fs.list", "cmd.fs.watch", "cmd.fs.unwatch", "cmd.fs.reveal",
    "cmd.window.spawnAt",
    "cmd.voice.mute", "cmd.voice.pushToTalk", "cmd.voice.setVoice", "cmd.scene.setMode",
    # ADDITIVE under CONTRACT §7.2 — a new cmd.* type does not bump
    # PROTOCOL_VERSION, because §3.2 requires unknown types to be ignored. The
    # §8 table entry is Gerald's to add; the diff is in the report.
    "cmd.calendar.today",
    # ADDITIVE under CONTRACT §7.2 — the vault's sub-namespace, taken from the
    # vault's own handler map so the two can never drift: cmd.vault.status,
    # .list, .unlock, .lock, .store, .remove, .setPolicy, .setPassphrase.
    # Handlers live in core/capabilities/vault/ws.py; the CONTRACT rows are the
    # owner's to add (VAULT-CONTRACT-DIFF.md). PROTOCOL_VERSION stays 1.
    *_VaultWs.COMMANDS,
})

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    ms = int(time.time() * 1000)
    time_part = ""
    for _ in range(10):
        time_part = _ULID_ALPHABET[ms % 32] + time_part
        ms //= 32
    rand = "".join(secrets.choice(_ULID_ALPHABET) for _ in range(16))
    return time_part + rand


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def envelope(msg_type: str, payload: dict[str, Any], corr: str | None = None) -> str:
    return json.dumps({
        "v": PROTOCOL_VERSION,
        "id": ulid(),
        "ts": now_iso(),
        "type": msg_type,
        "corr": corr,
        "payload": payload,
    }, ensure_ascii=False)


def valid_envelope(obj: Any) -> bool:
    """Structural check only — payload fields stay open (CONTRACT §3.2)."""
    if not isinstance(obj, dict):
        return False
    if obj.get("v") != PROTOCOL_VERSION:
        return False
    if not isinstance(obj.get("id"), str) or not ULID_RE.match(obj["id"]):
        return False
    if not isinstance(obj.get("ts"), str) or not TS_RE.match(obj["ts"]):
        return False
    if not isinstance(obj.get("type"), str) or not TYPE_RE.match(obj["type"]):
        return False
    corr = obj.get("corr", None)
    if corr is not None and (not isinstance(corr, str) or not ULID_RE.match(corr)):
        return False
    payload = obj.get("payload")
    return isinstance(payload, dict)


def topic_matches(subscriptions: Iterable[str], msg_type: str) -> bool:
    body = msg_type.split(".", 1)[1] if "." in msg_type else msg_type
    for sub in subscriptions:
        if sub in ("*", "**"):
            return True
        if sub.endswith("*"):
            if body.startswith(sub[:-1]):
                return True
        elif body == sub or body.startswith(sub + "."):
            return True
    return False


# ── daemon ────────────────────────────────────────────────────────────────────


#: The timezone today's calendar events are shown in. Lagos, UTC+1, no DST.
#: A surface may override it per request; this is only the default.
CALENDAR_TZ = "Africa/Lagos"


class TessaDaemon:
    def __init__(self, *, dev: bool = False) -> None:
        self.dev = dev
        self.token = rt.new_token()
        self.port: int | None = None
        self.started_at = time.monotonic()

        self.audit = AuditLog(ROOT / "data" / "audit.log")
        # THE CREDENTIAL VAULT. Locked at start, always: the passphrase-derived
        # key lives in memory only and dies with this process, so every restart
        # re-locks it. No `path=` — the default is %LOCALAPPDATA%\Tessa\vault.json,
        # ACL-locked to the owner + SYSTEM (core/capabilities/HOOK-PROPOSAL.md §2).
        # Construction never raises: DPAPI or store trouble marks it `broken`
        # and every operation then refuses, audited.
        self.vault = _Vault(audit=self.audit)
        self.vault_ws = _VaultWs(self.vault, envelope=envelope, broadcast=self.broadcast)
        # `vault.status` — the one registry TOOL the model may name (green,
        # metadata only). Registered here because the spec needs this instance;
        # core/tools/_validate() has already run, so its capability is in
        # permissions.yaml regardless. No tool returns a secret (VAULT-DESIGN §5).
        for _spec in _vault_specs(self.vault):
            _REGISTRY[_spec.name] = _spec
        self.guard = Guard(ROOT / "core" / "config" / "permissions.yaml")
        # THE INJECTION FENCE, and there is exactly ONE of it. CONTRACT §6.1's
        # `external_content_in_context` counter is only a control if every path
        # that can run a red-tier tool consults the same instance — a second
        # SessionContext somewhere would be a flag that is always clear and a
        # gate that always opens.
        self.session = SessionContext()
        # TYPED TURNS RUN AS TASKS, ONE AT A TIME — see `_h_agent_message`.
        # The lock keeps two quick messages in the order he sent them on the
        # one shared executor; the set keeps a running task referenced.
        self._typed_lock = asyncio.Lock()
        self._typed_tasks: set[asyncio.Task[None]] = set()
        # THE THREAD, loaded from disk at boot. A corrupt file starts empty
        # and is reported — it must never stop the daemon coming up.
        self.conversation = Conversation()
        # WHAT STRANGERS SAID, kept apart from the thread. One store, its own
        # file, UNVERIFIED by construction; bound here so the claims.* tools
        # can reach it and nothing else has to. core/brain/claims.py.
        self.claims = ClaimStore()
        bind_claims(self.claims, self.conversation)

        # THE BRAIN. Built here so `brain.engine` in settings.yaml is read once,
        # at boot, and reported at boot — including whether it is actually
        # usable. Constructing an adapter loads no model and opens no socket, so
        # this costs microseconds even for `local`.
        #
        # It is NOT replaced with a working engine when the selected one is
        # unusable. He finds out in the start-up log and, if he asks it
        # something, out loud. See core/brain/llm/__init__.py.
        self.settings = load_settings(ROOT / "core" / "config" / "settings.yaml")
        try:
            self.brain = make_engine(self.settings)
        except ValueError as exc:
            log(f"!! brain: {exc}")
            self.brain = None

        # HOW PEOPLE WRITE, kept as numbers — the third store, apart from the
        # thread and the claims. Needs settings for `style.owner_handle`, so
        # it is built after they load. `style.enabled: false` leaves it
        # unbound, which is the exact behaviour before it existed.
        # core/brain/style.py.
        self.style = (StyleStore(owner_handle=resolve_owner_handle(self.settings))
                      if style_enabled(self.settings) else None)
        bind_style(self.style)

        # WHICH claims are worth keeping: the selective filter in front of the
        # claim store, scoring each post of an X read against HIS topics (the
        # thread's user turns, read and never written, plus
        # `claims.filter.topics`). It decides note-or-skip and nothing else —
        # a kept claim is exactly as unverified as before, and only his own
        # word promotes one. `claims.filter.enabled: false` is Stage 1
        # exactly. core/brain/relevance.py.
        self.relevance = relevance_from_settings(self.settings, self.conversation)

        budget = self.settings.get("budget", {}) or {}
        health_cfg = self.settings.get("health", {}) or {}
        pty_cfg = self.settings.get("pty", {}) or {}

        self.budget_cap = float(budget.get("nightly_cap", 0.0))
        self.ledger = CostLedger(ROOT / str(budget.get("ledger_path", "data/cost-ledger.jsonl")))
        self.health = HealthCollector(
            self.started_at,
            HealthConfig(
                api_probe_interval_s=float(health_cfg.get("api_probe_interval_s", 60)),
                api_probe_host=str(health_cfg.get("api_probe_host", "api.anthropic.com")),
                api_probe_port=int(health_cfg.get("api_probe_port", 443)),
                api_probe_timeout_s=float(health_cfg.get("api_probe_timeout_s", 2.0)),
            ),
        )
        self.health_interval_s = float(health_cfg.get("interval_s", 5))
        self.grant_sweep_interval_s = float(pty_cfg.get("grant_sweep_interval_s", 15))
        # CONTRACT §6.5 — real lifecycle, not a dict that is written and never read.
        self.registry = GrantRegistry(ttl_s=float(pty_cfg.get("grant_ttl_s", 30)))

        # Push-to-talk gate. There is no wake word and no VAD: the key IS the
        # segment boundary (CONTRACT §5.3).
        #
        # TWO SEPARATE FACTS, deliberately not one flag:
        #   mic_stream_open — the microphone is LIVE and filling the ring buffer
        #   ptt_active      — the owner has CLAIMED a segment
        #
        # They used to be the same event, because the stream opened on key-down.
        # The ring buffer split them: the stream is now live whenever the daemon
        # is, so the owner can be not-speaking while the microphone is on. An
        # audit entry whose meaning quietly changed is worse than one that is
        # missing, so both are recorded and neither stands in for the other.
        self.ptt_active = False
        self.mic_stream_open = False
        self.mic_opened_at: float | None = None

        # A THIRD FACT, and it is not either of the two above.
        #
        #   mic_stream_open — the microphone is LIVE
        #   ptt_active      — a segment is being CAPTURED right now
        #   session.open    — he is in a CONVERSATION: when this segment ends,
        #                     the next one opens by itself
        #
        # The session is what makes "I don't want to be toggling to talk all the
        # time" true. It is process state and cannot survive a restart — see
        # core/voice/session.py.
        # NAMED `convo`, NOT `session`, AND THAT IS NOT COSMETIC.
        # `self.session` is already the injection fence (SessionContext, line
        # ~212). Assigning a ConversationSession over it silently replaced the
        # security control that stops a web page reaching a red-tier tool — the
        # fence would have been an object with no `check_tool` on it. Caught
        # before it ran, but it is exactly the kind of collision that survives
        # review, so the name states which session it is.
        from core.voice.session import ConversationSession

        self.convo = ConversationSession()

        # The voice loop. None until --voice is passed: loading Whisper costs
        # seconds and ~250 MB, and a daemon started for the Console should not
        # pay for a microphone nobody is going to press.
        self.voice: Any = None

        # sessionId -> the task waiting for a `killed` report after a revoke.
        self._revoke_watch: dict[str, asyncio.Task[None]] = {}

        # connection -> state
        self.clients: dict[ServerConnection, dict[str, Any]] = {}
        # Superseded by self.registry: `pty_sessions` and `grants` were plain
        # dicts that nothing ever read back. See core/pty/grants.py.

        self._failures: list[float] = []
        self._listener_disabled = False

    # ── rate limiting ────────────────────────────────────────────────────────

    def record_failure(self) -> None:
        """
        Count a failed AUTHENTICATION attempt — a bad token on an allowed Origin.

        Deliberately NOT called for Origin or path rejections. Those are the
        expected drive-by case: a webpage probing 127.0.0.1 is already fully
        blocked at the upgrade, and counting it here would let any hostile page
        lock the owner out of their own console with five requests. The lockout
        exists for token brute-force, which requires a caller that already
        cleared the Origin check.
        """
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t < FAILURE_WINDOW_S]
        self._failures.append(now)
        if len(self._failures) >= FAILURE_LIMIT:
            self._listener_disabled = True
            self.audit.append(
                actor="system", tool="auth.lockout", tier="red",
                summary=f"{FAILURE_LIMIT} failed auth attempts in {FAILURE_WINDOW_S:.0f}s "
                        f"— listener disabled until restart",
            )
            log("!! listener DISABLED after repeated auth failures — restart the daemon")

    @property
    def listener_disabled(self) -> bool:
        return self._listener_disabled

    # ── HTTP upgrade: Origin allowlist + path ────────────────────────────────

    def process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        """
        Runs BEFORE the WebSocket handshake completes. Returning a Response
        rejects the connection. This is where the drive-by browser attack dies.
        """
        if self._listener_disabled:
            return connection.respond(429, "listener disabled\n")

        path = request.path.split("?", 1)[0]
        if path != WS_PATH:
            return connection.respond(404, "not found\n")

        origin = request.headers.get("Origin")
        if origin not in ALLOWED_ORIGINS:
            # Logged, not counted — see record_failure().
            self.audit.append(
                actor="system", tool="auth.origin_rejected", tier="red",
                summary=f"Rejected WebSocket upgrade from disallowed Origin: {origin!r}",
                detail={"origin": origin, "userAgent": request.headers.get("User-Agent")},
            )
            log(f"REJECT origin={origin!r}")
            return connection.respond(403, "forbidden\n")

        return None

    # ── connection lifecycle ─────────────────────────────────────────────────

    async def handle(self, ws: ServerConnection) -> None:
        state: dict[str, Any] = {
            "authed": False, "surface": None, "subs": set(), "sessionId": None,
        }
        self.clients[ws] = state

        try:
            # CONTRACT §2.1 — valid cmd.hello within 3s or the socket closes.
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=HANDSHAKE_DEADLINE_S)
            except asyncio.TimeoutError:
                self.audit.append(
                    actor="system", tool="auth.handshake_timeout", tier="red",
                    summary="Connection closed: no cmd.hello within 3000ms",
                )
                log("REJECT handshake timeout")
                await ws.close(CLOSE_HANDSHAKE_TIMEOUT, "handshake timeout")
                return

            if not await self._do_hello(ws, state, raw):
                return

            async for raw in ws:
                await self._dispatch(ws, state, raw)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            # A surface that asked for a grant and then died must not leave a
            # live authorization behind. Redeemed grants are kept - their
            # session may still be running and still needs to be revocable.
            owner = state.get("sessionId") or ""
            if owner:
                for g in self.registry.drop_owner(owner):
                    self.audit.append(
                        actor="system", tool="pty.grant.orphaned", tier="none",
                        summary=(
                            f"Released unredeemed grant {g.grant_id[:8]} - "
                            f"the {state.get('surface')} connection that requested it disconnected"
                        ),
                        detail={"grantId": g.grant_id, "sessionId": g.session_id},
                    )
            self.clients.pop(ws, None)

    async def _do_hello(self, ws: ServerConnection, state: dict[str, Any], raw: Any) -> bool:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            await ws.close(CLOSE_UNAUTHORIZED, "bad hello")
            self.record_failure()
            return False

        if not valid_envelope(msg) or msg.get("type") != "cmd.hello":
            await ws.close(CLOSE_UNAUTHORIZED, "expected cmd.hello")
            self.record_failure()
            return False

        payload = msg["payload"]
        client_version = payload.get("protocolVersion")
        if client_version != PROTOCOL_VERSION:
            self.audit.append(
                actor="system", tool="auth.version_mismatch", tier="none",
                summary=f"Client protocolVersion {client_version} != {PROTOCOL_VERSION}",
            )
            await ws.close(CLOSE_PROTOCOL_MISMATCH, "protocol version mismatch")
            return False

        supplied = payload.get("token")
        # Timing-safe: a plain == would leak the token a byte at a time.
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied, self.token):
            self.record_failure()
            self.audit.append(
                actor="system", tool="auth.bad_token", tier="red",
                summary="Rejected cmd.hello with an invalid token",
                detail={"surface": payload.get("surface")},
            )
            log("REJECT bad token")
            await ws.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return False

        surface = payload.get("surface")
        if surface not in SURFACES:
            await ws.close(CLOSE_UNAUTHORIZED, "unknown surface")
            self.record_failure()
            return False

        state.update(authed=True, surface=surface, sessionId=ulid())
        self._failures.clear()

        await ws.send(envelope("res.hello", {
            "ok": True,
            "daemonVersion": DAEMON_VERSION,
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": ["pty.grant", "fs.list", "audit.query", "permissions.tiers", "vault"],
            "sessionId": state["sessionId"],
        }, corr=msg["id"]))

        # `system`, NOT "human". A surface presenting the launch token is the
        # daemon's own admission control succeeding — a process authenticated,
        # and the daemon cannot know whether a person was at the keyboard (the
        # surfaces reconnect on their own). Claiming "human" here put the
        # owner's name on an event he may not have caused.
        self.audit.append(
            actor="system", tool="auth.hello", tier="green",
            summary=f"{surface} connected",
            detail={"surface": surface, "surfaceVersion": payload.get("surfaceVersion")},
        )
        log(f"AUTH ok surface={surface} session={state['sessionId'][:8]}")
        return True

    async def _dispatch(self, ws: ServerConnection, state: dict[str, Any], raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope", "message": "not valid JSON", "retryable": False,
            }))
            return

        if not valid_envelope(msg):
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope",
                "message": "envelope failed structural validation",
                "retryable": False,
            }, corr=msg.get("id") if isinstance(msg, dict) else None))
            return

        mtype: str = msg["type"]
        corr: str = msg["id"]
        payload: dict[str, Any] = msg["payload"]

        if not state["authed"]:
            await ws.send(envelope("err.auth.required", {
                "code": "auth.required", "message": "send cmd.hello first", "retryable": False,
            }, corr=corr))
            return

        # CONTRACT §7.6 — unknown type is an error response, NOT a disconnect.
        if mtype not in KNOWN_COMMANDS:
            self.audit.append(
                actor="system", tool="protocol.unknownType", tier="none",
                summary=f"Unknown message type from {state['surface']}: {mtype}",
            )
            await ws.send(envelope("err.protocol.unknownType", {
                "code": "protocol.unknownType",
                "message": f"'{mtype}' is not defined in CONTRACT.md",
                "retryable": False,
            }, corr=corr))
            return

        handler = {
            "cmd.ping": self._h_ping,
            "cmd.subscribe": self._h_subscribe,
            "cmd.unsubscribe": self._h_unsubscribe,
            "cmd.pty.requestSpawn": self._h_pty_request_spawn,
            "cmd.pty.report": self._h_pty_report,
            "cmd.audit.query": self._h_audit_query,
            "cmd.permission.respond": self._h_permission_respond,
            "cmd.voice.pushToTalk": self._h_voice_push_to_talk,
            "cmd.agent.message": self._h_agent_message,
            "cmd.calendar.today": self._h_calendar_today,
            # cmd.vault.* — pure delegation to core/capabilities/vault/ws.py.
            # The secret-bearing fields are popped out of `payload` by the vault
            # before anything else runs; never log a cmd.vault.* frame.
            **self.vault_ws.handler_map(),
        }.get(mtype)

        if handler is None:
            await ws.send(envelope("err.internal", {
                "code": "internal",
                "message": f"'{mtype}' is in the contract but not yet implemented",
                "retryable": False,
            }, corr=corr))
            return

        await handler(ws, state, payload, corr)

    # ── handlers ─────────────────────────────────────────────────────────────

    async def _h_ping(self, ws, state, payload, corr) -> None:
        await ws.send(envelope("res.pong", {}, corr=corr))

    async def _h_calendar_today(self, ws, state, payload, corr) -> None:
        """
        Today's events for the Orb's TODAY panel. READ-ONLY, always.

        THE PANEL SHOWS WHAT IS TRUE OR IT SAYS SO. Three states, and the
        payload distinguishes all three without the surface having to guess:

            connected=true,  events=[...]   -> render them
            connected=true,  events=[]      -> "No events today" (a REAL answer)
            connected=false, reason="..."   -> "Not connected", and why

        `stale` plus `ageSeconds` cover the fourth case that is not a state:
        connected, but these are cached events served because the network is
        down. The panel can then say how old they are instead of implying they
        are current.

        The fetch is BLOCKING httpx and runs in a thread, because the daemon's
        event loop also carries the PTY grant path and the voice pipeline — a
        20-second Google timeout on the loop would stall both.
        """
        import asyncio

        from core.gcal.google import GoogleCalendar

        tz = str(payload.get("timezone") or CALENDAR_TZ)
        cal = str(payload.get("calendarId") or "primary")
        force = bool(payload.get("force"))

        def _work() -> dict:
            return GoogleCalendar(tz_name=tz, calendar_id=cal).today(force=force).to_payload()

        try:
            result = await asyncio.to_thread(_work)
        except Exception as e:  # noqa: BLE001 — a panel must never see a traceback
            log(f"calendar.today failed: {type(e).__name__}: {e}")
            result = {
                "connected": False, "events": [], "total": 0, "truncated": False,
                "fetchedAt": "", "ageSeconds": 0, "stale": False, "date": "",
                "timezone": tz, "tzSource": "", "calendarId": cal,
                "reason": "internal", "detail": f"{type(e).__name__}: {e}", "flagged": [],
            }

        # EVENT TITLES ARE EXTERNAL CONTENT (CONTRACT §6.1). Anything the
        # detector fired on is recorded in the audit log — reported, never
        # obeyed. The titles themselves are not written to the log: they are his
        # private calendar, and the audit file is not the place for them.
        if result.get("flagged"):
            self.audit.append(
                actor="system", tool="calendar.today", tier="none",
                summary=(
                    f"{len(result['flagged'])} injection pattern(s) in today's calendar "
                    f"titles - treated as data"
                ),
                detail={"patterns": result["flagged"], "calendarId": cal},
                provenance="external",
            )

        await ws.send(envelope("res.calendar.today", result, corr=corr))

    async def _h_subscribe(self, ws, state, payload, corr) -> None:
        topics = payload.get("topics") or []
        if isinstance(topics, list):
            state["subs"].update(str(t) for t in topics)
        await ws.send(envelope("res.subscribe", {"topics": sorted(state["subs"])}, corr=corr))

        # ── RE-ANNOUNCE WHAT IS STILL PENDING ────────────────────────────────
        #
        # Session 2 found this: a card raised before a reconnect vanished, and
        # the Orb came back with an empty list while Gerald believed something
        # was queued. Same class as the PTY roster gap — broadcast-only, never
        # snapshot-on-subscribe. Every `evt.*` that represents STATE rather than
        # an EVENT needs both, and a pending approval is state.
        #
        # Sent only to the subscriber that just asked, not broadcast, so a
        # second surface connecting does not re-flash a card the first is
        # already showing.
        gate = getattr(getattr(self, "voice", None), "executor", None)
        gate = getattr(gate, "approvals", None)
        if gate is not None and topic_matches(state["subs"], "evt.permission.request"):
            for req in gate.sweep_and_list():
                await ws.send(envelope("evt.permission.request", {
                    "requestId": req.request_id,
                    "tier": req.tier,
                    "tool": req.tool,
                    "args": req.args,
                    "provenance": req.provenance,
                    "expiresAt": _iso_in(APPROVAL_WINDOW_S - (time.monotonic() - req.at)),
                }))

    async def _h_unsubscribe(self, ws, state, payload, corr) -> None:
        for t in payload.get("topics") or []:
            state["subs"].discard(str(t))
        await ws.send(envelope("res.ok", {}, corr=corr))

    async def _h_pty_request_spawn(self, ws, state, payload, corr) -> None:
        """CONTRACT §6.5 — no PTY may be created without a grant."""
        actor = payload.get("actor")
        cwd = payload.get("cwd")
        profile_id = payload.get("profileId", "default")

        if actor not in ("human", "agent", "schedule") or not isinstance(cwd, str) or not cwd:
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope",
                "message": "requestSpawn needs actor ∈ {human,agent,schedule} and a cwd",
                "retryable": False,
            }, corr=corr))
            return

        decision = self.guard.evaluate_pty_spawn(actor, cwd)

        if decision.verdict is Verdict.DENY:
            self.audit.append(
                actor=actor, tool="pty.spawn", tier=decision.tier or "red",
                summary=f"DENIED shell in {cwd}: {decision.reason}",
                detail={"profileId": profile_id, "cwd": cwd}, provenance=actor,
            )
            await ws.send(envelope("err.permission.denied", {
                "code": "permission.denied", "message": decision.reason, "retryable": False,
            }, corr=corr))
            return

        if decision.verdict is Verdict.CONFIRM:
            request_id = ulid()
            # REGISTER IT, or the card cannot be answered.
            #
            # This path minted a requestId, audited it and broadcast
            # `evt.permission.request` — and never told the ApprovalGate. So a
            # surface rendering the card and replying `cmd.permission.respond`
            # got `err.notFound`, and the request sat there until it aged out of
            # nothing, because nothing was holding it. Broadcast-only again, the
            # same shape as the PTY roster gap and the pending-approval
            # snapshot.
            #
            # Registering it makes DENY work correctly and makes the request
            # visible to `sweep_and_list`, so it re-announces on subscribe and
            # expires on the timer like every other one. APPROVE is refused with
            # an honest message — the grant flow behind it is not wired to the
            # approval path yet, and an approve that silently did nothing would
            # be worse than one that says so.
            _gate = getattr(getattr(self, "voice", None), "executor", None)
            _gate = getattr(_gate, "approvals", None)
            if _gate is not None:
                _req = _gate.request(
                    tool="pty.spawn", args={"cwd": cwd, "profileId": profile_id},
                    tier=decision.tier or "amber", provenance=actor,
                    detail=f"shell in {cwd}: {decision.reason}")
                request_id = _req.request_id
            self.audit.append(
                actor=actor, tool="pty.spawn", tier=decision.tier or "amber",
                summary=f"Awaiting owner approval for shell in {cwd}: {decision.reason}",
                detail={"requestId": request_id, "cwd": cwd}, provenance=actor,
            )
            await ws.send(envelope("err.permission.pending", {
                "code": "permission.pending", "message": decision.reason, "retryable": True,
            }, corr=corr))
            await self.broadcast("evt.permission.request", {
                "requestId": request_id,
                "tier": decision.tier or "amber",
                "tool": "pty.spawn",
                "args": {"profileId": profile_id, "cwd": cwd},
                "provenance": actor,
                "expiresAt": now_iso(),
            })
            return

        grant_id, session_id = ulid(), ulid()
        grant = self.registry.issue(
            grant_id=grant_id,
            session_id=session_id,
            cwd=cwd,
            profile_id=profile_id,
            actor=actor,
            tier=decision.tier or "green",
            owner=state.get("sessionId") or "",
        )
        # A REAL deadline. The previous implementation sent `now_iso()` — the
        # current instant, already expired on arrival — and never checked it.
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=self.registry.ttl_s)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        self.audit.append(
            actor=actor, tool="pty.spawn", tier=decision.tier or "green",
            summary=f"Granted shell '{profile_id}' in {cwd} (ttl {self.registry.ttl_s:.0f}s)",
            # profileId is STRUCTURED here, not only inside the summary prose.
            # "Granted shell 'gitbash' in ..." could already answer which shell
            # ran something, but only by regex-parsing a human sentence. A field
            # is what makes `audit.query` able to answer it.
            detail={"grantId": grant_id, "sessionId": session_id, "cwd": cwd,
                    "profileId": profile_id, "expiresAt": expires_at},
            provenance=actor,
        )
        await ws.send(envelope("res.pty.grant", {
            "grantId": grant_id, "sessionId": session_id, "expiresAt": expires_at,
        }, corr=corr))
        log(f"GRANT {profile_id} in {cwd} -> session {session_id[:8]} "
            f"ttl {grant.remaining_s():.0f}s")

    async def _h_pty_report(self, ws, state, payload, corr) -> None:
        """
        Lifecycle reporting. This is where CONTRACT 6.5 is actually ENFORCED.

        `started` redeems the grant. A `started` whose session has no live,
        unexpired, unredeemed grant means a PTY exists that this daemon never
        authorized - a contract violation, audited at red tier and answered with
        a revoke, rather than quietly accepted into the roster.
        """
        session_id = payload.get("sessionId")
        event = payload.get("event")
        detail = payload.get("detail")

        if event not in PTY_REPORT_EVENTS or not isinstance(session_id, str):
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope",
                "message": f"bad report: event={event!r} sessionId={session_id!r}",
                "retryable": False,
            }, corr=corr))
            return

        if event == "started":
            result, grant = self.registry.redeem_by_session(session_id)
            if result is not RedeemResult.OK or grant is None:
                self.audit.append(
                    actor="system", tool="pty.started.unauthorized", tier="red",
                    summary=(
                        f"REFUSED pty.started for session {session_id[:8]}: "
                        f"grant {result.value}. A PTY may exist that was never granted."
                    ),
                    detail={"sessionId": session_id, "reason": result.value},
                )
                log(f"!! pty.started REFUSED session={session_id[:8]} reason={result.value}")
                await ws.send(envelope("err.permission.denied", {
                    "code": "permission.denied",
                    "message": f"no valid grant for session {session_id} ({result.value})",
                    "retryable": False,
                }, corr=corr))
                unauthorized_reason = f"unauthorized session - grant {result.value}"
                await self.broadcast("evt.pty.revoke", {
                    "sessionId": session_id,
                    "reason": unauthorized_reason,
                })
                # This path issues a revoke without going through
                # revoke_session(), so it arms the watchdog itself. Missing it
                # here would leave the enforcement path that fires MOST often as
                # the one path with no compliance check.
                self.arm_revoke_watchdog(session_id, unauthorized_reason)
                return

            self.registry.add_session(Session(
                session_id=session_id,
                grant_id=grant.grant_id,
                profile_id=grant.profile_id,
                cwd=grant.cwd,
                actor=grant.actor,
                started_at=now_iso(),
                pid=detail if isinstance(detail, int) else None,
            ))
            self.audit.append(
                actor=grant.actor, tool="pty.started", tier=grant.tier,
                summary=f"session {session_id[:8]} started in {grant.cwd} (grant redeemed)",
                detail={"sessionId": session_id, "grantId": grant.grant_id},
                provenance=grant.actor,
            )

        elif event == "startFailed":
            # THE reason this enum value exists: a grant issued for a PTY that
            # never came up must be released, or it strands an authorization.
            reclaimed = self.registry.reclaim(session_id)
            self.audit.append(
                actor="system", tool="pty.startFailed", tier="none",
                summary=(
                    f"session {session_id[:8]} failed to start; "
                    f"grant {'reclaimed' if reclaimed else 'not found'}"
                    + (f": {detail}" if detail else "")
                ),
                detail={"sessionId": session_id,
                        "grantId": reclaimed.grant_id if reclaimed else None},
            )
            log(f"startFailed session={session_id[:8]} grant "
                f"{'reclaimed ' + reclaimed.grant_id[:8] if reclaimed else 'NOT FOUND'}")

        elif event in ("exited", "killed"):
            # Either answers an outstanding revoke: `killed` is compliance, and
            # `exited` means the session is gone regardless of who ended it.
            self.disarm_revoke_watchdog(session_id)
            self.registry.end_session(session_id)
            # `killed` is the Console reporting that it complied with THIS
            # DAEMON'S `evt.pty.revoke` after observing the death (apps/console
            # pty-host.ts reports it from the revoke path and nowhere else). That
            # is the daemon's own action completing, so it is `system` — it used
            # to say "human", attributing a revoke he did not issue to him.
            self.audit.append(
                actor="program" if event == "exited" else "system",
                tool=f"pty.{event}", tier="none",
                summary=f"session {session_id[:8]} {event}" + (f": {detail}" if detail else ""),
                detail={"sessionId": session_id},
            )

        else:  # cwdChanged | titleChanged
            sess = self.registry.get_session(session_id)
            if sess is not None and isinstance(detail, str):
                if event == "cwdChanged":
                    sess.cwd = detail
                else:
                    sess.title = detail
            self.audit.append(
                actor="program", tool=f"pty.{event}", tier="none",
                summary=f"session {session_id[:8]} {event}" + (f": {detail}" if detail else ""),
                detail={"sessionId": session_id},
            )

        await ws.send(envelope("res.ok", {}, corr=corr))
        await self.broadcast("evt.pty.sessions", {"sessions": self.registry.wire_sessions()})

    async def revoke_session(self, session_id: str, reason: str) -> None:
        """Order a session killed (CONTRACT 4.2). The Console must comply and report back."""
        self.registry.end_session(session_id)
        self.audit.append(
            actor="system", tool="pty.revoke", tier="red",
            summary=f"Revoked session {session_id[:8]}: {reason}",
            detail={"sessionId": session_id, "reason": reason},
        )
        log(f"REVOKE session={session_id[:8]} reason={reason}")
        await self.broadcast("evt.pty.revoke", {"sessionId": session_id, "reason": reason})
        self.arm_revoke_watchdog(session_id, reason)

    async def _run_voice_turn(self) -> None:
        """
        Key-up to spoken answer. The whole §4 budget lives in here.

        Every state transition is broadcast, not just the endpoints: Session 2's
        sphere is driven by `evt.agent.state` and has nothing else to go on, so a
        loop that reported only `listening` and `idle` would leave the sphere
        frozen through THINKING — which is the slowest part and exactly when the
        owner most needs to see that something is happening.
        """
        try:
            turn = await asyncio.wait_for(
                asyncio.to_thread(self.voice.stop), timeout=VOICE_TURN_STALL_S)
        except asyncio.TimeoutError:
            stages = getattr(self.voice, "stages", [])
            last = stages[-1][0] if stages else "no stage reached"
            trail = " -> ".join(f"{n}@{ms:.0f}ms" for n, ms in stages) or "none"
            log(f"!! VOICE TURN STALLED after {VOICE_TURN_STALL_S:.0f}s | last stage: {last}")
            log(f"   stages: {trail}")
            self.audit.append(
                actor="system", tool="voice.turn.stalled", tier="red",
                summary=(f"voice turn did not complete within {VOICE_TURN_STALL_S:.0f}s; "
                         f"last stage reached: {last}"),
                detail={"lastStage": last, "stages": trail,
                        "timeoutS": VOICE_TURN_STALL_S},
            )
            await self.broadcast("evt.agent.state",
                                 {"companionId": DEFAULT_COMPANION_ID, "state": "idle"})
            return
        except Exception as exc:  # noqa: BLE001
            log(f"!! voice turn failed: {exc}")
            await self.broadcast("evt.agent.state", {"companionId": DEFAULT_COMPANION_ID, "state": "idle"})
            self.audit.append(
                actor="system", tool="voice.turn.failed", tier="amber",
                summary=f"voice turn failed: {exc}", detail={},
            )
            return
        if turn is None:
            await self.broadcast("evt.agent.state", {"companionId": DEFAULT_COMPANION_ID, "state": "idle"})
            return

        # An empty segment in an open session is the room being quiet, not a
        # turn. Counting it would make a session that sat idle overnight report
        # hundreds of "turns" in its closing audit line.
        if turn.heard or turn.said:
            self.convo.note_turn()
        # ── HE SAID HE IS DONE ───────────────────────────────────────────────
        #
        # Closed BEFORE the transcript is broadcast, so the `idle` that follows
        # her closing line cannot re-arm behind it. The ordering matters: with
        # the session still open, the re-arm hook would fire on that drain and
        # she would go quiet and then immediately start listening again, which
        # is the opposite of what he asked for and would look like a bug.
        if turn.ends_session:
            self.close_session("he said so")

        # `evt.transcript.message` — the WHOLE-TURN path, not `.delta`.
        # Delta is for token-by-token streaming from a model; these answers are
        # produced complete by a local handler, so emitting a single message is
        # the truthful shape. Sending deltas for text that never streamed would
        # fake a streaming source.
        #
        # SHAPE IS CONTRACT §4.1, EXACTLY:
        #   { companionId, message: { messageId, role, text, toolCalls?, ts } }
        #
        # This used to send a FLAT { role, text, final } and Session 2's handler
        # correctly rejected it on shape. Two messages were dropped per turn —
        # his line and hers — which is why TRACE and the under-sphere line were
        # empty while the voice loop worked end to end. `final` is gone: it was
        # never in the contract, and §3.2 means an unknown field is ignored
        # silently, which is exactly how a wrong shape survives unnoticed.
        #
        # `messageId` is a real ULID, not a placeholder: Session 2's reassembler
        # keys on it and so does their React list, so a duplicate or a missing id
        # is a rendering bug rather than a cosmetic one.
        if turn.heard:
            await self.broadcast("evt.transcript.message", {
                "companionId": DEFAULT_COMPANION_ID,
                "message": {
                    "messageId": ulid(), "role": "user",
                    "text": turn.heard, "ts": now_iso(),
                },
            })
        if turn.said:
            await self.broadcast("evt.transcript.message", {
                "companionId": DEFAULT_COMPANION_ID,
                "message": {
                    "messageId": ulid(), "role": "assistant",
                    "text": turn.said, "ts": now_iso(),
                },
            })

        t = turn.timing
        # TOOL OUTCOME IN THE LOG LINE. A successful call, a failed call and a
        # call that never dispatched previously looked identical here.
        tools = " | tools=" + (", ".join(
            f"{n}:{'ok' if ok else 'FAILED ' + err}" for n, ok, err in turn.tools
        ) if turn.tools else "none")
        # A PRIVATE turn (x.read_dm / x.send_dm, round 4, 2026-09-12): what
        # he said carries the message he dictated, what she said carries the
        # messages she read him. Neither is for the chain or the log — the
        # tool's own READ-DM / DM lines already record the handle, a count
        # and a digest. Withheld here, with the lengths, so the entry still
        # says a turn happened and what ran.
        _private = self._private_turn(turn.tools, turn.heard)
        _heard = (f"<withheld: {len(turn.heard)} chars, a private-message turn>"
                  if _private else turn.heard)
        _said = (f"<withheld: {len(turn.said)} chars, a private-message turn>"
                 if _private else turn.said)
        log(f"TURN heard={_heard!r} intent={turn.intent.value}{tools} | {t.describe()}")
        log(f"     said={_said!r}")
        self.audit.append(
            actor="human", tool=f"voice.turn.{turn.intent.value}", tier="green",
            summary=f"voice turn: heard {_heard[:60]!r} -> {turn.intent.value}",
            detail={
                "heard": _heard, "said": _said,
                "sttMs": round(t.stt_s * 1000), "ttsMs": round(t.tts_s * 1000),
                "toFirstAudioMs": round(t.total_to_first_audio_s * 1000),
            },
            provenance="human",
        )
        # Also no explicit broadcast: the bus emitted `speaking` when playback
        # started and will emit `idle` from `finished_callback` when the audio
        # actually drains. Re-emitting here duplicated the first and pre-empted
        # the second with a state that was not yet true.

    async def rearm_for_session(self) -> None:
        """
        Open the next segment without a keypress. The whole point of a session.

        Guarded rather than trusted: `_emit_state` fires on every transition and
        `idle` can arrive more than once per turn (an empty turn, a STOP, the
        drain after speaking). Re-arming twice would arm a microphone that is
        already armed and lose the pre-roll, so this checks the flags again on
        the event loop where they cannot race.
        """
        if not self.convo.open or self.ptt_active or self.voice is None:
            return
        loop_now = asyncio.get_running_loop()

        def _auto_stop(reason: str) -> None:
            log(f"  [turn] VAD closed the segment ({reason}) [session turn "
                f"{self.convo.turns + 1}]")
            self.ptt_active = False
            asyncio.run_coroutine_threadsafe(self._run_voice_turn(), loop_now)

        # FLUSH BEFORE ARMING. The ring has been filling the whole time she was
        # speaking and now holds her own voice. Without this the pre-roll
        # prepends her closing words to his next segment and she transcribes
        # herself. See ArmedMicrophone.flush_ring.
        dropped = await asyncio.to_thread(self.voice.mic.flush_ring)

        self.ptt_active = True
        await asyncio.to_thread(lambda: self.voice.start(on_auto_stop=_auto_stop))
        log(f"  [session] listening again — {self.convo.describe()} "
            f"(flushed {dropped / 16000:.2f}s of her own audio from the ring)")

    def close_session(self, reason: str) -> None:
        """
        End the conversation and audit it.

        BOTH ENDS ARE LOGGED. A session that ran four hours is a real fact about
        his machine — `voice.stream.open` is red tier for the same reason — and
        an open event with no close event would leave the log unable to answer
        "how long was the microphone listening", which is the only question that
        matters here.
        """
        if not self.convo.open:
            return
        detail = self.convo.end()
        detail["reason"] = reason
        self.ptt_active = False
        if self.voice is not None:
            self.voice.session_open = False
            # ITEM 3h: re-opening after a close must GREET, not acknowledge.
            # The close is him telling her he is going away, which is better
            # evidence than the 20-minute silence the return gap infers from.
            router = getattr(self.voice, "router", None)
            if router is not None and hasattr(router, "mark_conversation_closed"):
                router.mark_conversation_closed()
        self.audit.append(
            actor="human", tool="voice.session.end", tier="amber",
            summary=(f"conversation session closed after "
                     f"{detail['durationS']:.0f}s and {detail['turns']} turn(s) "
                     f"({reason})"),
            detail=detail, provenance="human",
        )
        log(f"  [session] CLOSED after {detail['durationS']:.0f}s, "
            f"{detail['turns']} turn(s) ({reason})")

    def open_session(self, by: str) -> None:
        """
        Start a conversation, once. Re-entering an open one is a no-op.

        ITEM 3c: him saying the wake phrase while a session is already open
        lands here and does nothing — no second session, no error. The greeting
        is separately suppressed by the router's return-gap rule, so he gets an
        acknowledgement rather than "Good evening" for the fourth time.
        """
        cfg = (self.settings.get("voice", {}) or {}).get("session", {}) or {}
        if not cfg.get("enabled", True):
            return
        # THE STRICT MODE, off by default. See settings.yaml for why.
        if cfg.get("require_verification"):
            sp = getattr(self.voice, "speaker", None) if self.voice else None
            if sp is None or not getattr(sp, "enrolled", False):
                log("  [session] refused — require_verification is on and there "
                    "is no usable voiceprint")
                self.audit.append(
                    actor="system", tool="voice.session.refused", tier="amber",
                    summary=("conversation session refused: verification "
                             "required but unavailable"),
                    detail={"openedBy": by}, provenance="system",
                )
                return
        if not self.convo.start(by):
            return
        if self.voice is not None:
            self.voice.session_open = True
        # E3: WHO OPENED IT. A chord is a key he pressed — `human`, the only
        # trusted source (CONTRACT §6.2). A wake phrase proves only that a
        # phrase was heard: a television or a recording can produce one, so it
        # is `program`, exactly as the adjacent `voice.wake.fired` entry already
        # records it. This used to write `human` for both.
        opener = "human" if by == "chord" else "program"
        self.audit.append(
            actor=opener, tool="voice.session.start", tier="amber",
            summary=(f"conversation session opened by {by} — the microphone "
                     f"will re-arm after every turn until he closes it"),
            detail={"openedBy": by}, provenance=opener,
        )
        log(f"  [session] OPEN (by {by}) — she will keep listening until you "
            f"say you are done")

    # ── microphone lifecycle (spec §7, CONTRACT §5.3) ────────────────────────

    def audit_mic_open(self, pre_roll_s: float, device: str) -> None:
        """
        The microphone went LIVE. Audited at RED, and the tier is the argument.

        Higher than `voice.ptt.start`'s amber, which will read as backwards
        until you consider what each event actually is. A segment claim is the
        owner deliberately speaking to his own machine — routine, consented,
        initiated by him. The stream opening is the machine beginning to listen
        to a room, indefinitely, without a further human act. He pressed no key
        for this one; it happened because the daemon started.

        Gerald's audit log is the record of what this machine did TO him as well
        as FOR him, and "the microphone has been live for nine hours" is exactly
        the kind of fact that should be impossible to miss when scanning it. Red
        is what makes it impossible to miss.
        """
        self.mic_stream_open = True
        self.mic_opened_at = time.monotonic()
        self.audit.append(
            actor="system", tool="voice.stream.open", tier="red",
            summary=(
                f"MICROPHONE LIVE - continuous capture began, {pre_roll_s:.1f}s rolling "
                f"pre-roll held in memory (device: {device})"
            ),
            detail={"preRollS": pre_roll_s, "device": device, "ringOnly": True},
        )
        log(f"!! MICROPHONE LIVE (pre-roll {pre_roll_s:.1f}s, device {device})")

    def audit_mic_close(self, reason: str) -> None:
        """The microphone went dead. Carries how long it was live, which is the
        fact worth reading — an open time without a duration is half a record."""
        if not self.mic_stream_open:
            return
        held = time.monotonic() - (self.mic_opened_at or time.monotonic())
        self.mic_stream_open = False
        self.mic_opened_at = None
        self.audit.append(
            actor="system", tool="voice.stream.close", tier="amber",
            summary=f"microphone closed after {held:.1f}s live ({reason})",
            detail={"heldS": round(held, 1), "reason": reason},
        )
        log(f"microphone closed after {held:.1f}s ({reason})")

    # ── revoke compliance ────────────────────────────────────────────────────

    def arm_revoke_watchdog(self, session_id: str, reason: str) -> None:
        """
        Record it here if a revoke is never confirmed.

        CONTRACT 6.5: "The daemon may revoke at any time... The Console must
        comply and report back." Compliance is reported with
        cmd.pty.report{killed} - and PtyReportEvent is a CLOSED enum (7.4) with
        no value meaning "ordered to kill, could not comply". So a Console whose
        kill ladder is exhausted has only two options: lie, or say nothing. Ours
        says nothing, deliberately - a false `killed` in a tamper-evident log is
        worse than an incomplete one.

        Nothing is the correct thing for the Console to send and the wrong thing
        for the audit trail to contain, which leaves a gap: a revoke that was
        never satisfied looks exactly like one that is still in flight.

        This closes it WITHOUT touching the frozen enum or bumping
        PROTOCOL_VERSION. The `tool` field of an audit entry is daemon-side and
        free-form; it is not protocol. And closing it here is strictly stronger
        than adding an enum value would be, because it does not depend on a
        Console self-reporting its own failure - a Console that has crashed,
        hung, or been tampered with reports nothing at all, and silence is
        precisely what this catches.
        """
        old = self._revoke_watch.pop(session_id, None)
        if old is not None:
            old.cancel()
        self._revoke_watch[session_id] = asyncio.create_task(
            self._watch_revoke(session_id, reason)
        )

    def disarm_revoke_watchdog(self, session_id: str) -> None:
        """Compliance confirmed (or the session ended on its own). Stand down."""
        task = self._revoke_watch.pop(session_id, None)
        if task is not None:
            task.cancel()

    async def _watch_revoke(self, session_id: str, reason: str) -> None:
        try:
            await asyncio.sleep(REVOKE_CONFIRM_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        self._revoke_watch.pop(session_id, None)
        entry = self.audit.append(
            actor="system", tool="pty.revoke.unsatisfied", tier="red",
            summary=(
                f"session {session_id[:8]} was ordered killed and NO killed report "
                f"arrived within {REVOKE_CONFIRM_TIMEOUT_S:.0f}s - COMPLIANCE UNCONFIRMED"
            ),
            detail={
                "sessionId": session_id,
                "reason": reason,
                "timeoutS": REVOKE_CONFIRM_TIMEOUT_S,
            },
        )
        log(f"!! REVOKE UNSATISFIED session={session_id[:8]} - no killed report in "
            f"{REVOKE_CONFIRM_TIMEOUT_S:.0f}s (audit seq {entry['seq']})")

    def _text_agent(self):
        """
        The Router and Executor a typed turn uses.

        REUSED FROM THE VOICE LOOP WHEN ONE EXISTS, and that is the point rather
        than an optimisation. Sharing the Executor means sharing the injection
        fence, the confirmation holds and the confirm ledger — so a hold opened
        by voice can be answered by keyboard, which is ruling 2 at the level
        that actually matters.

        Built standalone when the daemon runs without `--voice`, because typed
        chat must not depend on a microphone being present.
        """
        if self.voice is not None:
            return self.voice.router, self.voice.executor
        if getattr(self, "_typed_pair", None) is None:
            from core.brain.executor import Executor
            from core.brain.router import Router
            loop_ref = asyncio.get_running_loop()
            ex = Executor(
                session=self.session, audit=self.audit, claims=self.claims,
                style=self.style, relevance=self.relevance,
                on_permission_request=lambda pl: asyncio.run_coroutine_threadsafe(
                    self.broadcast("evt.permission.request", pl), loop_ref),
            )
            self._typed_pair = (
                Router(health_sample=lambda: self.health.sample(
                    budget_spent=self.ledger.spent_today(),
                    budget_cap=self.budget_cap)),
                ex,
            )
        return self._typed_pair

    async def _h_agent_message(self, ws, state, payload, corr) -> None:
        """
        A TYPED TURN. CONTRACT §7.2 additive — see the report's proposed diff.

        Everything a spoken turn does except the microphone and Piper:
        the router first, the brain for what it cannot place, the same memory,
        the same tiers, the same fence.

        SPEAKER VERIFICATION IS NOT RUN, deliberately. It gates the MICROPHONE,
        because a voice in the room is not proof of who is speaking. A keyboard
        in an authenticated session already carries that proof — the socket
        passed the per-launch token and the Origin check at `cmd.hello`. Running
        it here would fail closed with no voiceprint and lock him out of his own
        Console for a reason that does not apply.

        THE ACK IS IMMEDIATE (live-selector round, 2026-09-22). CONTRACT §5.1:
        `cmd.agent.message` answers `res.agent.accepted { messageId }` — an
        ACCEPTANCE, not the result. This used to reply only after the whole
        turn had run, and the Console's request timeout is 5 s: a turn that
        opens Chrome and waits for X to hydrate takes 10–25 s, so the pane
        said "no reply to cmd.agent.message within 5000 ms" while the answer
        was still on its way — and, because a connection's frames are
        dispatched in order, every other command on that socket (ping,
        permission.respond) queued behind the turn. Now the ack goes out
        first and the turn runs as a task under `_typed_lock`; the result
        reaches every subscribed surface as `evt.transcript.message`, exactly
        as it always did. The reply carries nothing the pane read.
        """
        text = str(payload.get("text") or "").strip()
        if not text:
            await ws.send(envelope("err.validation", {
                "code": "validation", "message": "text is required", "retryable": False,
            }, corr=corr))
            return

        message_id = ulid()
        # HIS LINE GOES OUT FIRST, so the pane can render it before she thinks.
        # Broadcast rather than replied, so a spoken turn and a typed turn land
        # on the SAME event and every subscribed surface sees one thread.
        await self.broadcast("evt.transcript.message", {
            "companionId": DEFAULT_COMPANION_ID,
            "message": {"messageId": message_id, "role": "user",
                        "text": text, "ts": now_iso(), "via": "typed"},
        })
        # THE ACK, BEFORE ANY THINKING. The same id as his line, so a surface
        # can tie the acceptance to the message it rendered.
        await ws.send(envelope("res.agent.accepted",
                               {"accepted": True, "messageId": message_id}, corr=corr))
        task = asyncio.create_task(self._typed_turn_task(text, message_id))
        self._typed_tasks.add(task)
        task.add_done_callback(self._typed_tasks.discard)

    async def _typed_turn_task(self, text: str, message_id: str) -> None:
        """One typed turn, serialised behind any turn already running."""
        async with self._typed_lock:
            try:
                await self._typed_turn(text, message_id)
            except Exception as exc:  # noqa: BLE001 — a task's exception is otherwise a log line at exit
                log(f"!! typed turn task failed: {type(exc).__name__}: {exc}")

    async def _typed_turn(self, text: str, message_id: str) -> None:
        """The turn itself — everything `_h_agent_message` did after the ack."""
        router, executor = self._text_agent()
        loop_ref = asyncio.get_running_loop()

        def _state(s: str) -> None:
            asyncio.run_coroutine_threadsafe(
                self.broadcast("evt.agent.state",
                               {"companionId": DEFAULT_COMPANION_ID, "state": s}),
                loop_ref)

        from core.brain.typed_turn import run_typed_turn

        try:
            turn = await asyncio.to_thread(
                run_typed_turn, text,
                router=router, executor=executor, brain=self.brain,
                conversation=self.conversation, on_state=_state, log=log,
            )
        except Exception as exc:  # noqa: BLE001 — a pane must never see a traceback
            log(f"!! typed turn failed: {type(exc).__name__}: {exc}")
            self.audit.append(
                actor="human", tool="agent.message", tier="amber",
                summary=f"typed turn failed: {type(exc).__name__}",
                detail={"via": "typed", "error": str(exc)[:200], "messageId": message_id},
                provenance="human",
            )
            await self.broadcast("evt.agent.state",
                                 {"companionId": DEFAULT_COMPANION_ID, "state": "idle"})
            # THE ACK HAS ALREADY GONE, so the pane hears about a failure the
            # way it hears an answer: a line in her register, on the thread.
            await self.broadcast("evt.transcript.message", {
                "companionId": DEFAULT_COMPANION_ID,
                "message": {"messageId": ulid(), "role": "assistant",
                            "text": ("Something went wrong while I was on that, Emperor. "
                                     "It is in the audit log; say it again and I will retry."),
                            "ts": now_iso(), "via": "typed"},
            })
            return

        # A TYPED TURN COUNTS AS A TURN. The session's turn counter is shared
        # with voice under ruling 2, so a conversation he continues by keyboard
        # does not look abandoned in the closing audit line.
        if turn.heard or turn.said:
            self.convo.note_turn()

        said = turn.said

        # ── RULING 3: RED POINTS AT THE ORB, AND SAYS SO IN THE RIGHT WORDS ──
        #
        # The shared refusal sentence ends "I am not doing it on your voice
        # alone", which is true for a spoken turn and simply wrong for one he
        # typed. It is rewritten here rather than in approvals.py so the voice
        # wording is untouched — and the honest half is the second sentence:
        # if the Orb is not connected there is no card to approve on, and
        # telling him to check one would send him looking for a window that is
        # not open.
        if turn.awaiting_approval:
            orb_up = any(
                (st.get("surface") == "orb") and st.get("authed")
                for st in self.clients.values()
            )
            head = said.split(" I am not doing it")[0].strip()
            if orb_up:
                said = (f"{head} That one needs your approval, Emperor. "
                        f"The card is on the Orb — approve it there and I will run it.")
            else:
                said = (f"{head} That one needs your approval, Emperor, and the Orb "
                        f"is not open. Start it and ask me again, and the card will "
                        f"be waiting for you.")

        if said:
            await self.broadcast("evt.transcript.message", {
                "companionId": DEFAULT_COMPANION_ID,
                "message": {"messageId": ulid(), "role": "assistant",
                            "text": said, "ts": now_iso(), "via": "typed"},
            })

        # THE AUDIT ENTRY NAMES THE KEYBOARD. `via` distinguishes it from voice
        # without a contract change: `detail` is a free-form object, the same
        # route `profileId` took for the PTY grant.
        self.audit.append(
            actor="human", tool="agent.message", tier="green",
            summary=(f"typed turn: {turn.intent}"
                     + (f", tools {', '.join(turn.tools)}" if turn.tools else "")
                     + (", awaiting approval on the Orb" if turn.awaiting_approval else "")),
            detail={"via": "typed", "intent": turn.intent, "tools": turn.tools,
                    "chars": len(turn.heard), "ms": round(turn.ms),
                    "injectionPatterns": turn.flagged},
            provenance="human",
        )
        log(f"TYPED {turn.intent} in {turn.ms:.0f} ms"
            + (f" tools={','.join(turn.tools)}" if turn.tools else ""))

        await self.broadcast("evt.agent.state",
                             {"companionId": DEFAULT_COMPANION_ID, "state": "idle"})

    async def _h_voice_push_to_talk(self, ws, state, payload, corr) -> None:
        """
        CONTRACT §5.3 `cmd.voice.pushToTalk` — Orb only, reserved, no payload
        specified. Proposed payload: `{ action: "start" | "stop" }`.

        Additive under §7.2, so no PROTOCOL_VERSION bump: a reserved command
        gaining a payload does not change any existing message.

        WHY `action` AND NOT A BARE TOGGLE. A toggle desynchronises the moment
        one frame is dropped or arrives late — the Orb thinks the key is down,
        the daemon thinks it is up, and the microphone is either dead or stuck
        open with no way to tell which. `start`/`stop` are idempotent and
        self-correcting: a repeated `start` is a no-op, and a `stop` that
        arrives without a matching `start` is answered rather than mis-applied.
        A stuck-open microphone is a privacy failure, not a UX one, so the
        design fails toward "not recording".

        SURFACE ENFORCEMENT is real, not cosmetic: §5.3 lists `voice.*` as Orb
        only, so a console-origin frame is refused. The Console has a terminal
        and no microphone; a voice command from it means either a bug or
        something wearing the Console's Origin.
        """
        surface = state.get("surface")
        if surface != "orb":
            await ws.send(envelope("err.permission.denied", {
                "code": "permission.denied",
                "message": f"cmd.voice.* is Orb-only (CONTRACT §5.3); this is '{surface}'",
                "retryable": False,
            }, corr=corr))
            self.audit.append(
                actor="system", tool="voice.pushToTalk.refused", tier="red",
                summary=f"REFUSED cmd.voice.pushToTalk from surface '{surface}' - Orb only (§5.3)",
                detail={"surface": surface},
            )
            return

        action = payload.get("action")
        if action not in ("start", "stop"):
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope",
                "message": "pushToTalk needs action in {start, stop}",
                "retryable": False,
            }, corr=corr))
            return

        was = self.ptt_active
        if action == "start":
            self.ptt_active = True
            # ITEM 2f: A SESSION STARTED BY KEYPRESS IS THE SAME SESSION.
            # One object, one lifecycle, one set of audit events — the entry
            # path is recorded in `openedBy` and changes nothing else. The
            # alternative, two kinds of session, is how two code paths drift.
            self.open_session("chord")
            if self.voice is not None:
                # NO explicit state broadcast here. `voice.start()` sets it on
                # the bus, and the bus is the single owner of the broadcast —
                # emitting it here as well is precisely what produced 2-3
                # `listening` frames per press on Session 2's wire.
                loop_now = asyncio.get_running_loop()

                def _auto_stop(reason: str) -> None:
                    # VAD closed the segment. Run the SAME turn path a manual
                    # second press would, so there is one code path and not two.
                    log(f"  [turn] VAD closed the segment ({reason})")
                    self.ptt_active = False
                    asyncio.run_coroutine_threadsafe(self._run_voice_turn(), loop_now)

                await asyncio.to_thread(lambda: self.voice.start(on_auto_stop=_auto_stop))
        else:
            self.ptt_active = False
            if self.voice is not None:
                # The whole turn runs off the event loop: STT is seconds of
                # blocking CPU and would otherwise stall every other client,
                # including the Orb's health stream and Session 2's sphere.
                asyncio.create_task(self._run_voice_turn())

        # Audited at amber: the owner's microphone opening and closing is a
        # privacy-relevant event and belongs in the same tamper-evident record
        # as everything else, not in a debug log.
        if was != self.ptt_active:
            self.audit.append(
                actor="human", tool=f"voice.ptt.{action}", tier="amber",
                summary=(
                    f"owner CLAIMED a voice segment ({action})"
                    if action == "start"
                    else "owner released the voice segment (stop)"
                ),
                detail={"action": action, "streamLive": self.mic_stream_open},
                provenance="human",
            )
            log(f"PTT {action} -> mic {'OPEN' if self.ptt_active else 'closed'}")

        await ws.send(envelope("res.ok", {
            "action": action,
            "active": self.ptt_active,
            "changed": was != self.ptt_active,
        }, corr=corr))

    def _private_turn(self, tools: Any, heard: str = "") -> bool:
        """
        True when a tool that ran this turn is PRIVATE (ToolSpec.private /
        private_args — x.read_dm, x.send_dm), OR when what he said has the
        SHAPE of a private message (core/brain/intents.is_private_utterance):
        the turn's heard and said text are then withheld from the chain and
        the log, because either would carry his private messages. The shape
        test covers the utterance the router missed — "direct message ada
        bob saying …" ran no tool and must not be logged either. Asked of the
        executor, which owns the registry; no executor, only the shape.
        """
        try:
            if heard and is_private_utterance(str(heard)):
                return True
        except Exception:  # noqa: BLE001
            pass
        ex = getattr(getattr(self, "voice", None), "executor", None)
        probe = getattr(ex, "is_private", None)
        if probe is None:
            return False
        try:
            return any(bool(probe(str(t[0] if isinstance(t, (tuple, list)) else t)))
                       for t in (tools or []))
        except Exception:  # noqa: BLE001
            return False

    async def _h_permission_respond(self, ws, state, payload, corr) -> None:
        """
        CONTRACT §5.1 — `cmd.permission.respond { requestId, decision, remember?,
        editedArgs? }`.

        THE COMMAND NAME IS `respond`, NOT `decide`. Session 2 built against
        `cmd.permission.decide`, which is not in the contract and would have come
        back `err.protocol.unknownType`. `respond` is what §5.1 defines and what
        this handler answers to.

        `editedArgs` IS THE NEW, OPTIONAL FIELD, and it is additive under §7.2 —
        a surface that never sends it behaves exactly as before.

        WHAT THIS HANDLER WILL NOT DO, stated as code rather than as comment:
        it never reads a tool, a tier, or a capability from the frame. Those come
        from the stored request. See `core/brain/approvals.resolve_edit`.
        """
        request_id = payload.get("requestId")
        decision = payload.get("decision")
        edited = payload.get("editedArgs")

        if not isinstance(request_id, str) or not request_id:
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope",
                "message": "requestId is required", "retryable": False}, corr=corr))
            return

        # §7.4: a surface may only ever SEND approve or deny. `expired` is
        # daemon-emitted and a surface claiming it would be asserting a fact
        # about a clock it does not own.
        if decision not in ("approve", "deny"):
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope",
                "message": "decision must be 'approve' or 'deny'",
                "retryable": False}, corr=corr))
            return

        executor = getattr(getattr(self, "voice", None), "executor", None)
        gate = getattr(executor, "approvals", None)
        pending = gate.pending.get(request_id) if gate else None
        if pending is None:
            await ws.send(envelope("err.notFound", {
                "code": "notFound",
                "message": f"no pending approval {request_id}",
                "retryable": False}, corr=corr))
            return

        # WHO INITIATED THE ACTION HE IS DECIDING ON — the value the card was
        # shown, resolved by the SAME `_actor_of` the executor uses at
        # dispatch. The deny path used to say "human" unconditionally, so an
        # agent-built request the owner denied was chained as his own act: the
        # mirror of the audit-actor bug, on the rejection path. His decision is
        # a human act; the ACTION was still initiated by whoever built the
        # call, and that is what `actor` means on the chain.
        #
        # HOISTED ABOVE THE BRANCH (E2). The APPROVAL REFUSED and APPROVAL
        # FAILED entries further down wrote a blanket `system`, so a terminal
        # entry for an agent-built request named nobody. Every outcome of this
        # decision — denied, refused, failed — now names the same initiator.
        origin = executor._actor_of(pending.provenance)

        if decision == "deny":
            gate.pending.pop(request_id, None)
            # A PRIVATE argument (x.send_dm's message, round 4) is withheld
            # on the deny path too: the executor's chain view swaps the
            # detail for the audit line and the value for its digest.
            _detail, _args = executor.chain_view(pending.tool, pending.args, pending.detail)
            self.audit.append(
                actor=origin, tool=pending.tool, tier=pending.tier,
                summary=f"DENIED {pending.tool}: {_detail}",
                detail={"requestId": request_id, "requestedArgs": _args},
                provenance=origin)
            # BROADCAST FIRST, REPLY SECOND, and the order is the fix.
            #
            # The reply goes to ONE socket; the broadcast tells the OTHER
            # surface to dismiss its card. Replying first meant that if the
            # decider's socket had dropped between the decision and the answer,
            # `ws.send` raised and the broadcast never ran — so the Console kept
            # showing a card for a request that had already been resolved, with
            # no way to learn otherwise. §4.1 says the resolution is broadcast
            # precisely so the other surface dismisses; that must not depend on
            # the decider still being there.
            await self.broadcast("evt.permission.resolved", {
                "requestId": request_id, "decision": "deny",
                "decidedBy": state.get("surface", "unknown"),
                "remembered": bool(payload.get("remember", False))})
            log(f"permission DENIED {pending.tool} ({request_id[:8]})")
            await ws.send(envelope("res.ok", {}, corr=corr))
            return

        # ── APPROVE ──────────────────────────────────────────────────────────
        #
        # TWO AUDIT ENTRIES, ALWAYS. What was REQUESTED and what was APPROVED
        # are different records and the second is the one that says what he
        # actually authorised. If he corrected a mangled tweet before sending,
        # the log holds both strings — the garbage Whisper produced and the
        # sentence he meant.
        # The REQUESTED/APPROVED pair is written by `execute_approved` itself,
        # so the record is identical whichever caller reached it.
        try:
            record = await asyncio.to_thread(
                executor.execute_approved, request_id, edited)
        except ApprovalError as err:
            self.audit.append(
                actor=origin, tool=pending.tool, tier=pending.tier,
                summary=f"APPROVAL REFUSED {pending.tool}: {err.message}",
                detail={"requestId": request_id}, provenance=origin)
            await ws.send(envelope(f"err.{err.code}", {
                "code": err.code, "message": err.message,
                "retryable": False}, corr=corr))
            log(f"permission REFUSED {pending.tool} ({request_id[:8]}): {err.message}")
            return
        except Exception as exc:  # noqa: BLE001
            self.audit.append(
                actor=origin, tool=pending.tool, tier=pending.tier,
                summary=f"APPROVAL FAILED {pending.tool}: {type(exc).__name__}",
                detail={"requestId": request_id}, provenance=origin)
            await ws.send(envelope("err.internal", {
                "code": "internal", "message": f"{type(exc).__name__}",
                "retryable": False}, corr=corr))
            return

        # Broadcast first — see the deny branch. The action has ALREADY run by
        # this point, so a dropped decider socket must not leave the other
        # surface showing a card for something that has happened.
        await self.broadcast("evt.permission.resolved", {
            "requestId": request_id, "decision": "approve",
            "decidedBy": state.get("surface", "unknown"),
            "remembered": bool(payload.get("remember", False))})
        log(f"permission APPROVED{' (EDITED)' if record['edited'] else ''} "
            f"{record['tool']} ({request_id[:8]})")
        await ws.send(envelope("res.ok", {"spoken": record["spoken"]}, corr=corr))

    async def _h_audit_query(self, ws, state, payload, corr) -> None:
        limit = int(payload.get("limit", 100))
        # CONTRACT 4.1 names the identity field `entryId`. On disk it is `seq`,
        # and it MUST stay `seq` there: _canonical() hashes every field, so
        # renaming it would invalidate the hash of every entry ever written and
        # break the chain at line 1. So the rename happens on the WIRE only.
        #
        # Additive on purpose - `seq` is left in place so a surface already
        # keying on it keeps working while both sides converge on `entryId`.
        #
        # !! WHOEVER IMPLEMENTS evt.audit.appended: use `entryId`, not `seq`.
        #    It is not emitted anywhere yet, so it can be right from the start
        #    instead of inheriting this mismatch and needing its own shim.
        entries = [{**e, "entryId": e["seq"]} for e in list(self.audit)[-limit:]]
        await ws.send(envelope("res.audit", {"entries": entries}, corr=corr))

    # ── broadcast + heartbeat ────────────────────────────────────────────────

    async def broadcast(self, msg_type: str, payload: dict[str, Any]) -> None:
        frame = envelope(msg_type, payload)
        dead = []
        for ws, state in list(self.clients.items()):
            if not state["authed"] or not topic_matches(state["subs"], msg_type):
                continue
            try:
                await ws.send(frame)
            except websockets.exceptions.ConnectionClosed:
                dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)

    async def sweep_approvals(self) -> None:
        """
        Drop approval requests whose 30-minute window has closed.

        WITHOUT THIS, `sweep()` only ran when a surface subscribed. Expiry was
        still enforced at execution time — a stale requestId could never be
        acted on — but the records themselves lingered until someone connected,
        which on a headless run is never.

        TESSA_CORE-spec §5 rule 5: a lapsed approval is `needsReview`, not
        `failed`. Nothing broke; nobody answered. So each one is audited as it
        goes rather than vanishing, and the surfaces are told so a card that can
        no longer be actioned stops being shown.
        """
        while True:
            await asyncio.sleep(60.0)
            executor = getattr(getattr(self, "voice", None), "executor", None)
            gate = getattr(executor, "approvals", None)
            if gate is None:
                continue
            for req in gate.sweep():
                # E2: the TRUE initiator of the lapsed request, resolved by the
                # same `_actor_of` the card and the executor use — not a blanket
                # `system`. The daemon noticed the lapse; it did not initiate
                # the action, and the chain must say who did.
                origin = executor._actor_of(req.provenance)
                # A PRIVATE argument (x.send_dm's message, round 4) is withheld
                # on the expiry path too. This entry carried the card's spoken
                # line and the raw args, so an unanswered DM card put the
                # words on the chain — the one path the deny/approve fix
                # missed. Same chain view as the deny path: the audit line
                # (length + digest) and the args with the text withheld.
                _detail, _args = executor.chain_view(req.tool, req.args, req.detail)
                self.audit.append(
                    actor=origin, tool=req.tool, tier=req.tier,
                    summary=f"EXPIRED unanswered approval {req.tool}: {_detail}",
                    detail={"requestId": req.request_id, "requestedArgs": _args},
                    provenance=origin)
                # CONTRACT §4.1: `expired` is daemon-emitted only, and this is
                # the daemon emitting it.
                await self.broadcast("evt.permission.resolved", {
                    "requestId": req.request_id, "decision": "expired",
                    "decidedBy": "daemon", "remembered": False})
                log(f"approval EXPIRED {req.tool} ({req.request_id[:8]})")

    #: How often the schedule is checked. See `sweep_schedule`.
    SCHEDULE_TICK_S = 60.0

    async def sweep_schedule(self) -> None:
        """
        Send anything he approved that is now due. THE FEATURE'S MISSING HALF.

        `core/system/schedule.py` and `core/system/abilities/x_schedule.py` were
        built and proven — enqueue-only-on-approval, grace window, fire-once,
        persistence across restarts — and then nothing ever called the tick. The
        queue filled, times passed, and no post was ever sent. This is the timer
        that was missing, and it is deliberately the whole of the change: every
        decision about WHAT to send stays in `fire_due`.

        ⚠⚠ THIS CALLS `schedule.fire_due()` AND DECIDES NOTHING ITSELF.
        No due-check, no grace arithmetic, no fired/missed bookkeeping, no post
        call. A second implementation of any of those would be a second posting
        path — one that had never been through the round-3 proof, and one that
        could drift from the proven one silently. `fire_due`'s DEFAULT posters
        are `x_tools.post`/`x_tools.reply` with the approval flag, so the send
        that happens here is byte for byte the send the scheduling proof
        exercised.

        ⚠ EVERYTHING RUNS IN A THREAD, AND THAT IS NOT A DETAIL.
        `fire_due` reads the queue off disk and then drives PLAYWRIGHT — a
        browser navigation that routinely takes several seconds and can take
        thirty. Awaiting that inline would block this event loop, which is the
        WebSocket to both surfaces AND the voice loop's turn handling: she would
        go deaf and unresponsive for the duration of every tweet she sent.
        `asyncio.to_thread` keeps the loop free, which is why the whole body is
        one offloaded call rather than a sequence of them.
        """
        from core.system import schedule

        complained = False
        while True:
            await asyncio.sleep(self.SCHEDULE_TICK_S)

            # The common case is an empty queue, and it costs one stat() rather
            # than building a store and parsing JSON every minute forever.
            if not schedule.DEFAULT_PATH.exists():
                continue

            def _tick() -> dict[str, Any]:
                # Constructed per tick, not held: another process — the
                # approval path inside this same daemon — appends to this file,
                # so a long-lived store would fire against a stale snapshot and
                # miss everything he approved since the daemon started.
                store = schedule.ScheduleStore()
                return schedule.fire_due(store)

            try:
                result = await asyncio.to_thread(_tick)
            except schedule.ScheduleError as exc:
                # A corrupt queue is reported ONCE. Repeating it every minute
                # would bury the log in the same line forever, and the condition
                # cannot resolve itself.
                if not complained:
                    complained = True
                    log(f"!! schedule unreadable, nothing will be sent: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                # A tick that throws must not kill the task and silently end
                # scheduling for the rest of the daemon's life.
                log(f"!! schedule tick failed: {type(exc).__name__}: {exc}")
                continue
            complained = False

            for item in result.get("sent", []):
                # ⚠ ACTOR `schedule`, NOT `human`. CONTRACT §6.2's vocabulary
                # has a value for exactly this: an unattended trigger. He
                # authorised the post — and the entry says which card did it,
                # by request id — but he did not press anything at 2pm, and an
                # audit line claiming he did would be a lie about provenance.
                self.audit.append(
                    actor="schedule", tool="system.x.post_tweet", tier="red",
                    summary=(f"SCHEDULED SENT {item.kind} at "
                             f"{time.strftime('%H:%M', time.localtime(item.at))}: "
                             f"{item.text[:80]}"),
                    detail={"itemId": item.id, "requestId": item.request_id,
                            "approvedAt": item.created},
                    provenance="schedule")
                log(f"schedule: SENT {item.kind} {item.id[:8]} "
                    f"(card {item.request_id[:8] or 'unknown'})")

            for item in result.get("missed", []):
                self.audit.append(
                    actor="schedule", tool="system.x.post_tweet", tier="red",
                    summary=(f"SCHEDULED MISSED {item.kind} — its time passed while "
                             f"nothing was running; NOT sent late: {item.text[:60]}"),
                    detail={"itemId": item.id, "requestId": item.request_id},
                    provenance="schedule")
                log(f"schedule: MISSED {item.id[:8]} — not sent late")

            for item in result.get("failed", []):
                self.audit.append(
                    actor="schedule", tool="system.x.post_tweet", tier="red",
                    summary=f"SCHEDULED FAILED {item.kind}: {item.note}",
                    detail={"itemId": item.id, "requestId": item.request_id},
                    provenance="schedule")
                log(f"schedule: FAILED {item.id[:8]} — {item.note}")

    async def heartbeat(self) -> None:
        """
        evt.daemon.health, CONTRACT 4.1.

        Exactly six fields, exactly those names. The Orb subscribes to daemon.*
        and renders from this, so a renamed or extra field is a cross-surface
        break, not a local refactor.

        Every value is now measured. They were previously literals - and
        publishing `apiReachable: false` from a literal is worse than publishing
        nothing, because it parks a permanent false alarm on the Orb's UI.

        The reachability probe is rate-limited inside HealthCollector (once per
        60 s, cached between beats), so this 5 s loop never waits on the network.
        `to_thread` keeps even that cached path off the event loop.
        """
        while True:
            await asyncio.sleep(self.health_interval_s)
            try:
                payload = await asyncio.to_thread(
                    self.health.sample,
                    budget_spent=self.ledger.spent_today(),
                    budget_cap=self.budget_cap,
                    brain_calls=getattr(self.brain, "calls", 0),
                    brain_engine=getattr(self.brain, "name", ""),
                    # Incremental chain verification, on the SAME worker thread
                    # as the rest of the sample so a file read never touches the
                    # event loop.
                    audit=self.audit,
                )
            except Exception as err:  # noqa: BLE001
                # A health sample must never kill the beat the Orb renders from.
                log(f"!! health sample failed: {err}")
                continue
            await self.broadcast("evt.daemon.health", payload)

    async def sweep_grants(self) -> None:
        """
        Drop unredeemed grants past their deadline (CONTRACT 6.5).

        Without this an expired grant would linger in memory forever. It is also
        the only place a "granted but never spawned" event becomes visible in
        the audit trail.
        """
        while True:
            await asyncio.sleep(self.grant_sweep_interval_s)
            for g in self.registry.sweep_expired():
                self.audit.append(
                    actor="system", tool="pty.grant.expired", tier="none",
                    summary=(
                        f"Grant {g.grant_id[:8]} for session {g.session_id[:8]} expired "
                        f"unredeemed after {self.registry.ttl_s:.0f}s"
                    ),
                    detail={"grantId": g.grant_id, "sessionId": g.session_id, "cwd": g.cwd},
                )
                log(f"grant expired unredeemed: {g.grant_id[:8]}")


def load_settings(path: Path) -> dict[str, Any]:
    """Read settings.yaml. Missing file is not fatal — defaults apply."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        log(f"!! {path} missing — using built-in defaults")
        return {}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def find_free_port(preferred: int, limit: int) -> int:
    for port in range(preferred, preferred + limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in {preferred}..{preferred + limit}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Tessa Core daemon")
    parser.add_argument("--dev", action="store_true", help="verbose dev mode")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--voice", action="store_true",
                        help="load the voice loop and open the microphone")
    parser.add_argument("--inject-wav", default=None,
                        help="DEV: feed a WAV into the mic callback as if spoken, then run a real turn")
    parser.add_argument("--dump-segments", action="store_true",
                        help="write each segment handed to Whisper under data/audio/segments/")
    parser.add_argument("--stt-model", default="base",
                        help="whisper size: tiny | base | small (default base)")
    args = parser.parse_args()

    # THE ONE STOP DOOR (core/lifecycle.py). Bound first, so a console close, a
    # sign-out or a tcli stop that lands during the ~20 s startup is queued and
    # honoured as soon as the socket is up, instead of being lost.
    stop = asyncio.Event()
    lifecycle.STOPPER.bind(asyncio.get_running_loop(), stop)

    # ── spec §7.5, BEFORE anything else exists ───────────────────────────────
    #
    # First, because a LocalSystem daemon writes its runtime file and its audit
    # log into a redirected profile. Checking after construction would mean the
    # check runs only once the damage is already on disk.
    #
    # Same rule as runtime.py's ACL readback: do not try and hope, check and
    # refuse. A daemon that cannot prove who it is does not start.
    try:
        identity = assert_not_service_account()
        log(f"identity: {identity['user']} ({identity['sid']}) | {identity['appdata']}")
    except IdentityError as exc:
        log(f"!! REFUSING TO START — {exc}")
        return

    daemon = TessaDaemon(dev=args.dev)
    port = args.port or find_free_port(PREFERRED_PORT, PORT_SCAN_LIMIT)
    daemon.port = port

    if not daemon.health.psutil_available:
        log("!! psutil unavailable - cpuPct/memMB will report 0.0")
    else:
        log(f"health: psutil active | budget cap {daemon.budget_cap:.2f} {CURRENCY} "
            f"| spent today {daemon.ledger.spent_today():.2f} {CURRENCY}")

    ok, why = daemon.audit.verify()
    if not ok:
        log(f"!! AUDIT CHAIN BROKEN: {why}")
    else:
        log(f"audit chain intact ({daemon.audit.count} entries)")

    # A browser orphaned by a force-killed previous daemon. Item 2c: a headful
    # Chrome must never outlive the daemon, and `atexit` does not run under
    # taskkill /F — which is exactly how this daemon usually dies.
    _reaped = reap_orphan()
    if _reaped:
        log(f"browser: {_reaped}")

    if daemon.conversation.load_error:
        log(f"memory: conversation.json UNREADABLE ({daemon.conversation.load_error}) "
            f"- starting with an empty thread")
    else:
        log(f"memory: {daemon.conversation.describe()} loaded")
    if daemon.claims.load_error:
        log(f"claims: claims.json UNREADABLE ({daemon.claims.load_error}) "
            f"- starting empty")
    else:
        log(f"claims: {daemon.claims.describe()} loaded (all external-untrusted)")
    if daemon.style is None:
        log("style: disabled in settings.yaml (style.enabled) - nothing learned")
    elif daemon.style.load_error:
        log(f"style: style.json UNREADABLE ({daemon.style.load_error}) - starting empty")
    else:
        log(f"style: {daemon.style.describe()} (patterns only)")
    if daemon.relevance is None:
        log("claims filter: off (claims.filter.enabled) - every claim from a read is noted")
    else:
        log(f"claims filter: on - {daemon.relevance.describe()}; "
            f"relevance decides note-or-skip only, never truth")

    _persona_ok, _persona_path = persona_loaded()
    log(f"brain: persona tessa.md {'loaded' if _persona_ok else 'MISSING'} "
        f"({len(persona_system_prompt())} chars)")

    for _name, _sel, _why, _usable in describe_engines(daemon.settings):
        _mark = "->" if _sel else "  "
        _state = "ready" if _usable else f"UNUSABLE ({_why})"
        log(f"brain: {_mark} {_name:<10} {_state}")

    # ── WARM THE APPLICATION INDEX ───────────────────────────────────────────
    #
    # Fast tier inline (~45 ms: both Start Menu roots, both Desktops, App Paths,
    # shell built-ins), slow tier on a background thread (~2-4 s: Get-StartApps
    # for UWP packages, and resolving every shortcut target so a dead one is
    # never offered). Cached to data/appindex.json, so a normal restart reloads
    # 440 entries in ~16 ms and builds nothing.
    #
    # Started HERE rather than lazily on his first "open X" so that the one
    # command he uses most never pays for the index.
    try:
        from core.brain.appindex import warm as _warm_apps

        _idx = _warm_apps(background=True)
        log(f"apps: index warming - {len(_idx.entries)} entries ready, "
            f"UWP tier building in the background")
    except Exception as exc:  # noqa: BLE001
        log(f"apps: index unavailable ({exc}) - she will fall back per query")

    # THE ACL GATE RUNS NOW; THE ADVERTISEMENT DOES NOT.
    #
    # `runtime.json` is how every surface finds this daemon, so writing it is
    # the act of saying "I am up". It used to be written HERE — before the
    # Whisper load below and ~17 s before `serve()` binds a socket — which meant
    # that for the whole of that window the file advertised a port nothing was
    # listening on. A surface that polled it got ECONNREFUSED, and anything that
    # sampled the pid recorded a ~65 MB daemon that was about to be ~314 MB.
    # That window IS the 5x memMB discrepancy Session 2 reported.
    #
    # The ACL verification stays at the front, because refusing to start on a
    # world-readable token file must not cost a 17 s model load first.
    # ── THE RENAME MIGRATION, BEFORE ANYTHING READS THAT DIRECTORY ───────────
    #
    # `%LOCALAPPDATA%\Zoey` -> `%LOCALAPPDATA%\Tessa`. It must run before the
    # ACL preflight below, before the browser tool resolves its profile root,
    # and before anything else touches the path — a half-migrated directory is
    # the one state that loses his logged-in X session.
    #
    # Logged plainly, once, whether or not it did anything: a migration that
    # runs silently is one he cannot verify happened.
    _moved = rt.migrate_local_appdata()
    if _moved:
        log(f"migration: {_moved}")

    rt.preflight_runtime_file()
    log("runtime file: ACL verified; advertisement deferred until the socket is bound")
    log(f"allowed origins: {', '.join(sorted(ALLOWED_ORIGINS))}")
    if args.dev:
        # CONTRACT §2.3: "The daemon NEVER logs the token value, in any log
        # level." This previously printed the token itself under --dev, which is
        # exactly the leak that clause forbids — a token written to a log once is
        # leaked permanently, and stdout here is routinely redirected to a file.
        # A digest is enough to correlate a client's credential with the daemon's
        # without disclosing it.
        digest = hashlib.sha256(daemon.token.encode()).hexdigest()[:16]
        log(f"[dev] token digest: {digest} (value never logged)")

    if args.voice:
        from core.brain.router import Router
        from core.bus import AudioBus
        from core.voice.audio_io import ArmedMicrophone
        from core.voice.loop import VoiceLoop
        from core.voice.stt import WhisperSTT
        from core.voice.tts.piper_tts import PiperTTS

        # ── THE VOICEPRINT ───────────────────────────────────────────────────
        #
        # Constructed BEFORE Whisper so the boot log says plainly whether she is
        # verifying or not. An open session listens with no keypress, and the
        # only thing standing between that and "a microphone open to the room"
        # is this — so its state is announced rather than assumed.
        _speaker = None
        try:
            from core.voice.speaker import SpeakerVerifier

            _sv = SpeakerVerifier()
            if _sv.load():
                _speaker = _sv
                log(f"voice: {_sv.describe()}")
                if not _sv.enrolled:
                    log("voice: NO VOICEPRINT — every voice is accepted. Run "
                        "`python -m core.voice.speaker --enrol` before using "
                        "a conversation session in a room with other people.")
            else:
                log(f"voice: speaker verification UNAVAILABLE ({_sv.load_error}) "
                    f"- every voice will be accepted")
        except Exception as exc:  # noqa: BLE001
            log(f"voice: speaker verification unavailable ({exc})")

        log(f"voice: loading whisper '{args.stt_model}' int8 ...")
        t_v = time.monotonic()
        mic = ArmedMicrophone(pre_roll_s=1.0)
        stt = WhisperSTT(size=args.stt_model, compute_type="int8")
        tts = PiperTTS()
        loop_ref = asyncio.get_running_loop()

        def _emit_state(state: str, detail: dict | None = None) -> None:
            # Called from the worker thread; hop back to the loop to broadcast.
            #
            # CONTRACT §4.1 already declares `detail?`, so including it is
            # additive and does not move PROTOCOL_VERSION. It is OMITTED rather
            # than sent as null when there is nothing to say — §3.2 makes an
            # absent optional field the normal case, and a surface switching on
            # its presence should not have to distinguish "absent" from "null".
            payload: dict[str, Any] = {
                "companionId": DEFAULT_COMPANION_ID, "state": state,
            }

            # ── "IN A CONVERSATION" IS NOT AN AgentState ─────────────────────
            #
            # ITEM 6's answer. `AgentState` is a CLOSED enum (CONTRACT §7.4), so
            # adding `conversing` would be a BREAKING change: a version bump and
            # both surfaces shipping together, for something that is not a state
            # at all. She is `listening`, `thinking`, `speaking` or `working`
            # DURING a conversation — the session is orthogonal to all four and
            # persists across every one of them.
            #
            # So it rides in `detail`, which §4.1 already declares, which makes
            # it additive with no version bump. It is attached to EVERY state
            # event rather than only to transitions that happen to carry a tool,
            # because an indicator that only updates sometimes will drift — and
            # this one has to stay accurate for hours.
            det = dict(detail or {})
            det["session"] = {
                "open": daemon.convo.open,
                "turns": daemon.convo.turns,
                "openedBy": daemon.convo.opened_by,
            }
            payload["detail"] = det
            asyncio.run_coroutine_threadsafe(
                daemon.broadcast("evt.agent.state", payload), loop_ref
            )

            # ── THE RE-ARM, AND `idle` IS THE ONLY SAFE MOMENT ──────────────
            #
            # `idle` is emitted by AudioBus from the output stream's
            # `finished_callback` — the instant her audio actually DRAINS. Any
            # earlier and the segment is open while she is still talking: Piper
            # leaves the speaker, the microphone picks it up, Whisper
            # transcribes it, and she answers herself in a loop. There is no
            # acoustic echo cancellation here, which is exactly why this waits.
            if (state == "idle" and daemon.convo.open
                    and not daemon.ptt_active and daemon.voice is not None):
                asyncio.run_coroutine_threadsafe(daemon.rearm_for_session(), loop_ref)

        bus = AudioBus(on_state=lambda s, d=None: _emit_state(s.value, d))
        daemon.voice = VoiceLoop(
            mic=mic, stt=stt, tts=tts,
            router=Router(health_sample=lambda: daemon.health.sample(
                budget_spent=daemon.ledger.spent_today(),
                budget_cap=daemon.budget_cap)),
            bus=bus, on_state=_emit_state,
            on_stage=lambda name, ms: log(f"  [turn] {ms:8.1f} ms  {name}"),
            on_turn_timing=lambda payload: asyncio.run_coroutine_threadsafe(
                daemon.broadcast("evt.turn.timing",
                                 {"companionId": DEFAULT_COMPANION_ID, **payload}),
                loop_ref),
            dump_segments=args.dump_segments,
            session=daemon.session, audit=daemon.audit, brain=daemon.brain,
            conversation=daemon.conversation, speaker=_speaker, claims=daemon.claims,
            style=daemon.style, relevance=daemon.relevance,
        )
        # CONTRACT §4.1 `evt.permission.request`. The red gate raises these and
        # nothing can answer them yet — the approval card is P5 and Session 2's.
        # Emitting them anyway is deliberate: the moment that card exists, red
        # tools work with no daemon change, and until then the requests are
        # visible on the wire and in the audit log rather than invisible.
        daemon.voice.executor.approvals._on_request = (
            lambda payload: asyncio.run_coroutine_threadsafe(
                daemon.broadcast("evt.permission.request", payload), loop_ref)
        )
        daemon.voice.vad_config = daemon.settings.get("voice", {}) or {}
        # PRE-WARM PIPER. Constructing PiperTTS loads the voice but does not run
        # an inference, so the first real synthesis pays the ONNX lazy init —
        # measured at 709 ms first call versus 338 ms warm on a quiet machine.
        # Gerald should pay that at boot, alongside the Whisper load he already
        # waits through, not on his first command.
        #
        # It is NOT the 12.4 s his turn saw. That synthesis ran immediately after
        # os.startfile spawned Explorer and Defender began scanning the opened
        # folder, on two cores — contention, not cold start. This removes a real
        # but much smaller penalty and is worth having regardless.
        _t_warm = time.monotonic()
        try:
            tts.synthesise("Ready.")
            log(f"voice: piper pre-warmed in {(time.monotonic() - _t_warm) * 1000:.0f} ms")
        except Exception as exc:  # noqa: BLE001
            log(f"voice: piper pre-warm failed ({exc}) - first reply will be slower")

        # ── THE WAKE PHRASE ──────────────────────────────────────────────────
        #
        # OFF unless settings.yaml says otherwise, and off by default there,
        # because the only model that exists says "hey jarvis" rather than her
        # name. The plumbing is complete so that swapping in a Colab-trained
        # hey_tessa.onnx is one config line and no code change.
        _wake_cfg = (daemon.settings.get("voice", {}) or {}).get("wake", {}) or {}
        if _wake_cfg.get("enabled"):
            from core.voice.wake import WakeDetector, chime as _wake_chime

            _model = (_wake_cfg.get("model") or "").strip()
            if _model and not Path(_model).is_absolute():
                _model = str(Path(__file__).resolve().parents[1] / _model)

            detector = WakeDetector(
                model_path=_model or None,
                threshold=float(_wake_cfg.get("threshold", 0.5)),
                refractory_s=float(_wake_cfg.get("refractory_s", 2.0)),
                # THE COEXISTENCE RULE (item 1e), and it is one line because the
                # daemon already owns the answer. `ptt_active` is true from the
                # moment the chord is pressed or a wake segment opens, so the
                # detector declines in BOTH directions: the chord pressed while
                # it is listening, and the phrase spoken inside an open segment.
                # One flag, one source of truth, no second state machine.
                is_armed=lambda: daemon.ptt_active,
            )
            if detector.load():
                _use_chime = bool(_wake_cfg.get("chime"))
                _loop_w = asyncio.get_running_loop()

                async def _open_wake_segment(evt) -> None:
                    # THE CHIME GOES IN FRONT OF THE ARM, NOT BEHIND IT.
                    # `voice.start()` barges in on the bus and then arms, so a
                    # chime played after arming would land in `_captured` — the
                    # only audio the VAD's `loudest` tracker reads — and raise
                    # the relative silence threshold. Played here it lands in the
                    # pre-roll, which the VAD never looks at. Nothing is lost
                    # from his command either: the pre-roll reaches 1.0 s
                    # BACKWARDS, so the phrase and everything after it is already
                    # in the ring before this runs.
                    if _use_chime:
                        try:
                            bus.speak(_wake_chime(), 16_000, blocking=True)
                        except Exception as exc:  # noqa: BLE001
                            log(f"  [wake] chime failed: {exc}")

                    def _auto_stop(reason: str) -> None:
                        log(f"  [turn] VAD closed the WAKE segment ({reason})")
                        daemon.ptt_active = False
                        asyncio.run_coroutine_threadsafe(
                            daemon._run_voice_turn(), _loop_w)

                    daemon.ptt_active = True
                    # The wake phrase opens a conversation, not one turn.
                    daemon.open_session("wake")
                    await asyncio.to_thread(
                        lambda: daemon.voice.start(on_auto_stop=_auto_stop))

                    # AUDITED SEPARATELY FROM voice.ptt.start, AND THIS IS THE
                    # RULING FOR ITEM 1g.
                    #
                    # `voice.ptt.start` means "the owner pressed a key", which is
                    # the only thing in this system that PROVES he is present.
                    # A wake segment proves that a phrase was heard, which anyone
                    # in the room, a television or a recording can produce. Those
                    # are different facts about his machine and folding them into
                    # one audit tool name would make the log unable to answer
                    # "did HE start this?" — the one question the log exists for.
                    #
                    # Provenance is `program`, not `human`, deliberately.
                    # CONTRACT §6.2 makes `human` the only trusted source and a
                    # wake phrase has not earned it. It becomes `human` only once
                    # speaker verification passes on the segment.
                    daemon.audit.append(
                        actor="system", tool="voice.wake.fired", tier="amber",
                        summary=(f"wake phrase '{evt.phrase}' detected "
                                 f"(score {evt.score:.3f}) - segment opened"),
                        detail={"phrase": evt.phrase,
                                "score": round(evt.score, 4),
                                "threshold": detector.threshold},
                        provenance="program",
                    )

                def _on_wake(evt) -> None:
                    # Runs INSIDE the audio callback. Do nothing here but hand
                    # off — any real work in this thread stalls the stream that
                    # push-to-talk also depends on.
                    log(f"  [wake] '{evt.phrase}' score={evt.score:.3f} "
                        f"decide={evt.decide_ms:.1f} ms")
                    asyncio.run_coroutine_threadsafe(
                        _open_wake_segment(evt), _loop_w)

                detector._on_wake = _on_wake
                mic.on_block = detector.feed
                daemon.voice.wake = detector

                log(f"voice: wake phrase ARMED - '{detector.phrase}' "
                    f"threshold {detector.threshold} "
                    f"chime={'on' if _use_chime else 'off'}")
                if not _model:
                    log("voice: WARNING - no wake model configured, using the "
                        "'hey_jarvis' proxy. She will not answer to her name.")
                # CONTINUOUS DETECTION IS A DIFFERENT FACT FROM AN ARMED RING.
                # Audited once at boot, at amber, because from here every sound
                # in the room is being evaluated rather than merely buffered.
                daemon.audit.append(
                    actor="system", tool="voice.wake.armed", tier="amber",
                    summary=(f"continuous wake detection ON - phrase "
                             f"'{detector.phrase}', every sound in the room is "
                             f"now evaluated, not just buffered"),
                    detail={"phrase": detector.phrase,
                            "threshold": detector.threshold,
                            "model": _model or "bundled hey_jarvis proxy"},
                    provenance="system",
                )
            else:
                log(f"voice: wake phrase UNAVAILABLE ({detector.load_error}) "
                    f"- push-to-talk is unaffected")

        mic.open()
        daemon.audit_mic_open(pre_roll_s=1.0, device="default input")

        if args.inject_wav:
            # DEV ONLY. Feeds a recorded WAV into the SAME callback the sound
            # device feeds, bypassing only the acoustics.
            #
            # This is not a harness and not a loopback. The ring, the pre-roll,
            # the VAD watcher, transcribe, route, execute, synthesise and
            # playback are all the daemon's own, running in the daemon. The only
            # substitution is the air between a speaker and a microphone —
            # which had to go, because acoustic echo cancellation destroys it
            # and makes every transcript from that route meaningless.
            import wave as _wave

            async def _inject() -> None:
                await asyncio.sleep(1.0)
                with _wave.open(args.inject_wav, "rb") as fh:
                    raw = fh.readframes(fh.getnframes())
                pcm = __import__("numpy").frombuffer(raw, dtype="int16")
                # Stop the real device first. Otherwise the live microphone
                # keeps feeding the SAME buffer and the segment becomes injected
                # audio interleaved with the room - measured once as 12.64 s of
                # chop for 5.10 s of injection, which Whisper returned empty.
                # The callback is a pure function; closing the device does not
                # stop it being callable.
                mic.close()
                log(f"INJECT: {args.inject_wav} ({len(pcm) / 16000:.2f}s) into the mic callback "
                    f"(real device closed so the segment is the WAV alone)")

                loop_ref2 = asyncio.get_running_loop()

                def _auto(reason: str) -> None:
                    log(f"  [turn] VAD closed the segment ({reason})")
                    daemon.ptt_active = False
                    asyncio.run_coroutine_threadsafe(daemon._run_voice_turn(), loop_ref2)

                daemon.voice.start(on_auto_stop=_auto)
                daemon.ptt_active = True
                # Real-time pacing in 50 ms blocks, so VAD sees the same
                # arrival cadence a live speaker produces.
                blk = 800
                for i in range(0, len(pcm), blk):
                    if not daemon.ptt_active:
                        log(f"INJECT: stopped early at {i / 16000:.2f}s (VAD closed it)")
                        break
                    mic._callback(pcm[i:i + blk].reshape(-1, 1), blk, None, None)
                    await asyncio.sleep(blk / 16000.0)

            asyncio.create_task(_inject())
        log(f"voice: ready in {time.monotonic() - t_v:.1f}s "
            f"(stt={args.stt_model} tts={tts.voice.voice_id})")

    daemon.audit.append(
        actor="system", tool="daemon.start", tier="none",
        summary=f"Daemon started on port {port}",
        detail={"protocolVersion": PROTOCOL_VERSION, "daemonVersion": DAEMON_VERSION},
    )

    def shutdown(signum: int, *_: Any) -> None:
        lifecycle.STOPPER.request(f"signal {signal.Signals(signum).name}")

    try:
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
    except (ValueError, AttributeError):
        pass

    async with serve(
        daemon.handle,
        host="127.0.0.1",              # CONTRACT §1 — loopback only, never 0.0.0.0
        port=port,
        process_request=daemon.process_request,
        max_size=MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
        # Bounds the close handshake. A surface that never answers a close must
        # not hold the clean shutdown past the 4.5 s a console close allows.
        close_timeout=2.0,
    ):
        # NOW the daemon exists. The socket is bound and accepting, Whisper and
        # Piper are resident, and the brain has been selected — so a surface
        # that reads this file and connects will succeed.
        #
        # This is the ONLY place runtime.json is written. Discoverable now
        # implies connectable, which was not true when it was written before the
        # model load.
        runtime_file = rt.write_runtime_file(port, daemon.token)
        log(f"runtime file: {runtime_file}")
        log(f"listening on ws://127.0.0.1:{port}{WS_PATH}")

        hb = asyncio.create_task(daemon.heartbeat())
        sweeper = asyncio.create_task(daemon.sweep_grants())
        approval_sweeper = asyncio.create_task(daemon.sweep_approvals())
        # The schedule's timer. Without this task the queue fills and nothing is
        # ever sent — see `sweep_schedule`.
        schedule_ticker = asyncio.create_task(daemon.sweep_schedule())
        # `tcli daemon stop` — a local request that proves this launch's token.
        stop_watch = asyncio.create_task(lifecycle.watch_stop_requests(
            rt.local_appdata_root(), daemon.token, daemon.audit))
        log_cap = asyncio.create_task(lifecycle.log_cap_task(ROOT / "data" / "logs"))
        try:
            await stop.wait()
        finally:
            hb.cancel()
            sweeper.cancel()
            approval_sweeper.cancel()
            schedule_ticker.cancel()
            stop_watch.cancel()
            log_cap.cancel()

        reason = lifecycle.STOPPER.reason or "stop"
        restarting = lifecycle.STOPPER.restarting
        log(f"shutdown: {reason} - stopping cleanly"
            f"{' (a restart follows)' if restarting else ''}")
        # CONTRACT §4 `evt.daemon.shutdown`, sent while the sockets are still
        # open, so a surface shows "reconnecting" rather than a dead link.
        try:
            await daemon.broadcast("evt.daemon.shutdown",
                                   {"reason": reason, "restarting": restarting})
        except Exception as exc:  # noqa: BLE001
            log(f"!! shutdown broadcast failed: {type(exc).__name__}: {exc}")

    # THE TAIL. Each step is guarded on its own: an exception in one once skipped
    # both the daemon.stop row and the runtime.json removal (see close_browser).
    # The audit row and the runtime file go FIRST — under a console close the
    # whole tail has 4.5 s, and those two are what the next start relies on.
    try:
        daemon.vault.lock("daemon.stop")     # zeroes the derived key; audited as LOCKED (daemon.stop)
    except Exception as exc:  # noqa: BLE001
        log(f"!! vault lock on shutdown failed: {type(exc).__name__}: {exc}")
    try:
        daemon.audit.append(actor="system", tool="daemon.stop", tier="none",
                            summary="Daemon stopped cleanly",
                            detail={"reason": reason, "restarting": restarting})
    except Exception as exc:  # noqa: BLE001
        log(f"!! daemon.stop audit failed: {type(exc).__name__}: {exc}")
    try:
        if rt.remove_runtime_file(daemon.token):
            log("stopped; runtime file removed")
        else:
            log("stopped; runtime file left alone - it does not name this daemon")
    except Exception as exc:  # noqa: BLE001
        log(f"!! runtime file removal failed: {type(exc).__name__}: {exc}")
    try:
        _closed = close_browser_on_shutdown(reason="daemon shutdown")
        if _closed.get("was_open"):
            log(f"browser: closed on shutdown (up {_closed.get('up_s')}s)")
    except Exception as exc:  # noqa: BLE001
        log(f"!! browser close on shutdown failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    # THE MAIN PATH ONLY. A multiprocessing child re-imports this module as
    # `__mp_main__`, so nothing below can run in one: no handler, no guard.
    #
    # ONE DAEMON PER DATA DIR AND PER RUNTIME DIR, decided by OWNING the guard
    # on this main thread — before a port is bound, the audit log opened or
    # runtime.json touched. Two overlapping daemons forked the audit chain at
    # seq 69 and, on 28 Sep, un-advertised a live daemon. A refused launch
    # writes one line and exits 3; it binds and writes nothing else.
    _data_dir = ROOT / "data"
    _guard, _held = lifecycle.take_guard(_data_dir, rt.local_appdata_root())
    if _guard is None:
        lifecycle.refuse_second_launch(rt.runtime_path(), _data_dir / "logs", _held)
    lifecycle.after_guard(guard=_guard, data_dir=_data_dir, runtime_dir=rt.local_appdata_root(),
                          runtime_file=rt.runtime_path(), flags=sys.argv[1:],
                          for_disable_inherited=_FOR_DISABLE_INHERITED)
    _code, _why = 0, "main returned (see the lines above)"
    try:
        asyncio.run(main())
        if lifecycle.STOPPER.reason:
            _why = f"clean stop - {lifecycle.STOPPER.reason}"
    except KeyboardInterrupt:
        _why = "KeyboardInterrupt"
    except SystemExit as _exc:
        _code = _exc.code if isinstance(_exc.code, int) else 1
        _why = f"exit requested ({_exc.code})"
    except BaseException as _exc:  # noqa: BLE001 - recorded, then the process ends
        import traceback

        traceback.print_exc()
        _code, _why = lifecycle.EXIT_CRASHED, f"CRASHED - {type(_exc).__name__}: {_exc}"
    lifecycle.finish(_guard, _code, _why)
