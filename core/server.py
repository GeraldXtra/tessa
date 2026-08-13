"""
core/server.py — Zoey Core daemon: the local WebSocket server.

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

import argparse
import asyncio
import hashlib
import json
import re
import secrets
import signal
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import websockets
import yaml
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
# Generated from packages/protocol/schema/enums.json — the single source of
# truth shared with TypeScript. Never hand-maintain a second copy here.
sys.path.insert(0, str(ROOT_DIR / "packages" / "protocol" / "gen" / "python"))

from zoey_protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    PTY_REPORT_EVENTS,
    SURFACES,
    CLOSE_UNAUTHORIZED,
    CLOSE_HANDSHAKE_TIMEOUT,
    CLOSE_PROTOCOL_MISMATCH,
    CLOSE_RATE_LIMITED,
)
from core.security.audit import AuditLog  # noqa: E402
from core.security.identity import IdentityError, assert_not_service_account  # noqa: E402
from core.security.guard import Guard, Verdict  # noqa: E402
from core.security import runtime as rt  # noqa: E402
from core.pty.grants import GrantRegistry, RedeemResult, Session  # noqa: E402
from core.telemetry.cost import CURRENCY, CostLedger  # noqa: E402
from core.telemetry.health import HealthCollector, HealthConfig  # noqa: E402

# ── constants from CONTRACT ───────────────────────────────────────────────────

DAEMON_VERSION = "0.1.0"

#: CONTRACT §4.1 keys `evt.agent.state`, `evt.transcript.*` and the companion
#: roster by `companionId`. One companion exists today; naming it explicitly now
#: is what stops the field being invented later, per-call and inconsistently.
DEFAULT_COMPANION_ID = "zoey"
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
WS_PATH = "/v1"
ALLOWED_ORIGINS = frozenset({"zoey://console", "zoey://orb"})
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


class ZoeyDaemon:
    def __init__(self, *, dev: bool = False) -> None:
        self.dev = dev
        self.token = rt.new_token()
        self.port: int | None = None
        self.started_at = time.monotonic()

        self.audit = AuditLog(ROOT / "data" / "audit.log")
        self.guard = Guard(ROOT / "core" / "config" / "permissions.yaml")
        self.settings = load_settings(ROOT / "core" / "config" / "settings.yaml")

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
            "capabilities": ["pty.grant", "fs.list", "audit.query", "permissions.tiers"],
            "sessionId": state["sessionId"],
        }, corr=msg["id"]))

        self.audit.append(
            actor="human", tool="auth.hello", tier="green",
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
            "cmd.voice.pushToTalk": self._h_voice_push_to_talk,
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

    async def _h_subscribe(self, ws, state, payload, corr) -> None:
        topics = payload.get("topics") or []
        if isinstance(topics, list):
            state["subs"].update(str(t) for t in topics)
        await ws.send(envelope("res.subscribe", {"topics": sorted(state["subs"])}, corr=corr))

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
            detail={"grantId": grant_id, "sessionId": session_id, "cwd": cwd,
                    "expiresAt": expires_at},
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
            self.audit.append(
                actor="program" if event == "exited" else "human",
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
            turn = await asyncio.to_thread(self.voice.stop)
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
        log(f"TURN heard={turn.heard!r} intent={turn.intent.value} | {t.describe()}")
        self.audit.append(
            actor="human", tool=f"voice.turn.{turn.intent.value}", tier="green",
            summary=f"voice turn: heard {turn.heard[:60]!r} -> {turn.intent.value}",
            detail={
                "heard": turn.heard, "said": turn.said,
                "sttMs": round(t.stt_s * 1000), "ttsMs": round(t.tts_s * 1000),
                "toFirstAudioMs": round(t.total_to_first_audio_s * 1000),
            },
            provenance="human",
        )
        await self.broadcast("evt.agent.state", {"companionId": DEFAULT_COMPANION_ID,
                                                 "state": "speaking" if turn.said else "idle"})

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
            if self.voice is not None:
                await asyncio.to_thread(self.voice.start)
                await self.broadcast("evt.agent.state", {"companionId": DEFAULT_COMPANION_ID, "state": "listening"})
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
    parser = argparse.ArgumentParser(description="Zoey Core daemon")
    parser.add_argument("--dev", action="store_true", help="verbose dev mode")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--voice", action="store_true",
                        help="load the voice loop and open the microphone")
    parser.add_argument("--stt-model", default="base",
                        help="whisper size: tiny | base | small (default base)")
    args = parser.parse_args()

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

    daemon = ZoeyDaemon(dev=args.dev)
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

    runtime_file = rt.write_runtime_file(port, daemon.token)
    log(f"runtime file: {runtime_file}")
    log(f"listening on ws://127.0.0.1:{port}{WS_PATH}")
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

        log(f"voice: loading whisper '{args.stt_model}' int8 ...")
        t_v = time.monotonic()
        mic = ArmedMicrophone(pre_roll_s=1.0)
        stt = WhisperSTT(size=args.stt_model, compute_type="int8")
        tts = PiperTTS()
        loop_ref = asyncio.get_running_loop()

        def _emit_state(state: str) -> None:
            # Called from the worker thread; hop back to the loop to broadcast.
            asyncio.run_coroutine_threadsafe(
                daemon.broadcast("evt.agent.state",
                                 {"companionId": DEFAULT_COMPANION_ID, "state": state}), loop_ref
            )

        bus = AudioBus(on_state=lambda s: _emit_state(s.value))
        daemon.voice = VoiceLoop(
            mic=mic, stt=stt, tts=tts,
            router=Router(health_sample=lambda: daemon.health.sample(
                budget_spent=daemon.ledger.spent_today(),
                budget_cap=daemon.budget_cap)),
            bus=bus, on_state=_emit_state,
        )
        mic.open()
        daemon.audit_mic_open(pre_roll_s=1.0, device="default input")
        log(f"voice: ready in {time.monotonic() - t_v:.1f}s "
            f"(stt={args.stt_model} tts={tts.voice.voice_id})")

    daemon.audit.append(
        actor="system", tool="daemon.start", tier="none",
        summary=f"Daemon started on port {port}",
        detail={"protocolVersion": PROTOCOL_VERSION, "daemonVersion": DAEMON_VERSION},
    )

    stop = asyncio.Event()

    def shutdown(*_: Any) -> None:
        stop.set()

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
    ):
        hb = asyncio.create_task(daemon.heartbeat())
        sweeper = asyncio.create_task(daemon.sweep_grants())
        try:
            await stop.wait()
        finally:
            hb.cancel()
            sweeper.cancel()

    daemon.audit.append(actor="system", tool="daemon.stop", tier="none",
                        summary="Daemon stopped cleanly")
    rt.remove_runtime_file()
    log("stopped; runtime file removed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
