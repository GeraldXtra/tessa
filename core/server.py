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
import json
import re
import secrets
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.security.audit import AuditLog  # noqa: E402
from core.security.guard import Guard, Verdict  # noqa: E402
from core.security import runtime as rt  # noqa: E402

# ── constants from CONTRACT ───────────────────────────────────────────────────

PROTOCOL_VERSION = 1
DAEMON_VERSION = "0.1.0"
PREFERRED_PORT = 47600
PORT_SCAN_LIMIT = 20
WS_PATH = "/v1"
ALLOWED_ORIGINS = frozenset({"zoey://console", "zoey://orb"})
HANDSHAKE_DEADLINE_S = 3.0
MAX_FRAME_BYTES = 1024 * 1024

CLOSE_UNAUTHORIZED = 4401
CLOSE_HANDSHAKE_TIMEOUT = 4408
CLOSE_PROTOCOL_MISMATCH = 4409
CLOSE_RATE_LIMITED = 4429

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

        # connection -> state
        self.clients: dict[ServerConnection, dict[str, Any]] = {}
        # sessionId -> session info, assembled from cmd.pty.report
        self.pty_sessions: dict[str, dict[str, Any]] = {}
        # outstanding spawn grants
        self.grants: dict[str, dict[str, Any]] = {}

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
        if surface not in ("console", "orb"):
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
        self.grants[grant_id] = {
            "sessionId": session_id, "cwd": cwd, "profileId": profile_id,
            "actor": actor, "issuedAt": time.monotonic(),
        }
        self.audit.append(
            actor=actor, tool="pty.spawn", tier=decision.tier or "green",
            summary=f"Granted shell '{profile_id}' in {cwd}",
            detail={"grantId": grant_id, "sessionId": session_id, "cwd": cwd},
            provenance=actor,
        )
        await ws.send(envelope("res.pty.grant", {
            "grantId": grant_id, "sessionId": session_id, "expiresAt": now_iso(),
        }, corr=corr))
        log(f"GRANT {profile_id} in {cwd} -> {session_id[:8]}")

    async def _h_pty_report(self, ws, state, payload, corr) -> None:
        """Lifecycle reporting keeps the audit log complete even though the
        daemon never sees the byte stream (CONTRACT §4.2)."""
        session_id = payload.get("sessionId")
        event = payload.get("event")
        if event not in ("started", "exited", "cwdChanged", "titleChanged", "killed"):
            await ws.send(envelope("err.protocol.badEnvelope", {
                "code": "protocol.badEnvelope", "message": f"bad report event {event!r}",
                "retryable": False,
            }, corr=corr))
            return

        info = self.pty_sessions.setdefault(session_id, {
            "sessionId": session_id, "profileId": "?", "cwd": "?",
            "title": "", "startedAt": now_iso(), "busy": False,
        })
        detail = payload.get("detail")
        if event == "cwdChanged" and detail:
            info["cwd"] = detail
        elif event == "titleChanged" and detail:
            info["title"] = detail
        elif event in ("exited", "killed"):
            self.pty_sessions.pop(session_id, None)

        self.audit.append(
            actor="program" if event in ("exited", "cwdChanged", "titleChanged") else "human",
            tool=f"pty.{event}", tier="none",
            summary=f"session {session_id[:8]} {event}" + (f": {detail}" if detail else ""),
            detail={"sessionId": session_id},
        )
        await ws.send(envelope("res.ok", {}, corr=corr))
        await self.broadcast("evt.pty.sessions", {"sessions": list(self.pty_sessions.values())})

    async def _h_audit_query(self, ws, state, payload, corr) -> None:
        limit = int(payload.get("limit", 100))
        entries = list(self.audit)[-limit:]
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
        while True:
            await asyncio.sleep(5)
            await self.broadcast("evt.daemon.health", {
                "uptimeS": round(time.monotonic() - self.started_at, 1),
                "cpuPct": 0.0, "memMB": 0.0,
                "apiReachable": False,
                "budgetSpent": 0.0, "budgetCap": 0.0,
            })


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
    args = parser.parse_args()

    daemon = ZoeyDaemon(dev=args.dev)
    port = args.port or find_free_port(PREFERRED_PORT, PORT_SCAN_LIMIT)
    daemon.port = port

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
        log(f"[dev] token: {daemon.token}")

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
        try:
            await stop.wait()
        finally:
            hb.cancel()

    daemon.audit.append(actor="system", tool="daemon.stop", tier="none",
                        summary="Daemon stopped cleanly")
    rt.remove_runtime_file()
    log("stopped; runtime file removed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
