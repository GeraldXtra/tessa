"""
core/test_auth.py — proves the Phase 0 exit criterion.

Every one of these is an attack that must fail, or a legitimate client that must
succeed. Run the daemon first, then this:

    python core/server.py --dev          (terminal 1)
    python core/test_auth.py             (terminal 2)

It reads the port and token from runtime.json, exactly as a real surface does.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.security import runtime as rt  # noqa: E402

PROTOCOL_VERSION = 1
_ALPHA = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

passed = failed = 0


def ulid() -> str:
    ms = int(time.time() * 1000)
    t = ""
    for _ in range(10):
        t = _ALPHA[ms % 32] + t
        ms //= 32
    return t + "".join(secrets.choice(_ALPHA) for _ in range(16))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def env(mtype: str, payload: dict, corr: str | None = None) -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "id": ulid(), "ts": now_iso(),
                       "type": mtype, "corr": corr, "payload": payload})


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


async def connect(url: str, origin: str | None):
    headers = {"Origin": origin} if origin is not None else {}
    return await websockets.connect(url, additional_headers=headers, open_timeout=5)


async def main() -> int:
    info = rt.read_runtime_file()
    if not info:
        print("No live runtime.json. Start the daemon first:  python core/server.py --dev")
        return 2

    port, token = info["port"], info["token"]
    url = f"ws://127.0.0.1:{port}/v1"
    print(f"\nTessa Core auth boundary — {url}\n")

    # ── 1. the drive-by browser attack ───────────────────────────────────────
    for bad_origin in ("http://evil.com", "https://example.com", "http://localhost:3000",
                       "file://", "null"):
        try:
            ws = await connect(url, bad_origin)
            await ws.close()
            check(f"Origin {bad_origin!r} rejected", False, "connection was ACCEPTED")
        except Exception as e:
            check(f"Origin {bad_origin!r} rejected", "403" in str(e) or "rejected" in str(e).lower(),
                  f"unexpected: {type(e).__name__}: {e}")

    # ── 2. missing Origin entirely ───────────────────────────────────────────
    try:
        ws = await connect(url, None)
        await ws.close()
        check("missing Origin rejected", False, "connection was ACCEPTED")
    except Exception as e:
        check("missing Origin rejected", "403" in str(e) or "rejected" in str(e).lower(), str(e))

    # ── 3. wrong path ────────────────────────────────────────────────────────
    try:
        ws = await connect(f"ws://127.0.0.1:{port}/", "tessa://console")
        await ws.close()
        check("wrong path rejected", False, "connection was ACCEPTED")
    except Exception as e:
        check("wrong path rejected", "404" in str(e) or "rejected" in str(e).lower(), str(e))

    # ── 3b. probing must NOT lock the owner out (DoS regression) ─────────────
    # Seven rejections just happened above. If Origin rejections counted toward
    # the auth-failure lockout, a hostile page could disable the owner's own
    # console with five requests. The legitimate client must still get in.
    try:
        async with await connect(url, "tessa://console") as ws:
            check("probing does not lock out the owner", True)
    except Exception as e:
        check("probing does not lock out the owner", False,
              f"legitimate client blocked after probes: {e}")

    # ── 4. bad token ─────────────────────────────────────────────────────────
    async with await connect(url, "tessa://console") as ws:
        await ws.send(env("cmd.hello", {"token": "0" * 64, "surface": "console",
                                        "surfaceVersion": "0.1.0",
                                        "protocolVersion": PROTOCOL_VERSION}))
        try:
            await asyncio.wait_for(ws.recv(), timeout=3)
            check("bad token closes 4401", False, "got a response instead of a close")
        except websockets.exceptions.ConnectionClosed as e:
            check("bad token closes 4401", e.code == 4401, f"got close code {e.code}")
        except asyncio.TimeoutError:
            check("bad token closes 4401", False, "timed out")

    # ── 5. protocol version mismatch ─────────────────────────────────────────
    async with await connect(url, "tessa://console") as ws:
        await ws.send(env("cmd.hello", {"token": token, "surface": "console",
                                        "surfaceVersion": "0.1.0", "protocolVersion": 99}))
        try:
            await asyncio.wait_for(ws.recv(), timeout=3)
            check("version mismatch closes 4409", False, "got a response")
        except websockets.exceptions.ConnectionClosed as e:
            check("version mismatch closes 4409", e.code == 4409, f"got {e.code}")
        except asyncio.TimeoutError:
            check("version mismatch closes 4409", False, "timed out")

    # ── 6. handshake deadline ────────────────────────────────────────────────
    t0 = time.monotonic()
    async with await connect(url, "tessa://console") as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=6)
            check("silent client closes 4408", False, "got a response")
        except websockets.exceptions.ConnectionClosed as e:
            elapsed = time.monotonic() - t0
            check("silent client closes 4408", e.code == 4408, f"got {e.code}")
            check("closed at ~3s", 2.5 <= elapsed <= 4.5, f"took {elapsed:.2f}s")
        except asyncio.TimeoutError:
            check("silent client closes 4408", False, "never closed")

    # ── 7. the happy path ────────────────────────────────────────────────────
    async with await connect(url, "tessa://console") as ws:
        hello_id = ulid()
        await ws.send(json.dumps({"v": 1, "id": hello_id, "ts": now_iso(), "type": "cmd.hello",
                                  "corr": None,
                                  "payload": {"token": token, "surface": "console",
                                              "surfaceVersion": "0.1.0",
                                              "protocolVersion": PROTOCOL_VERSION}}))
        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("valid client gets res.hello", res["type"] == "res.hello", str(res)[:120])
        check("res.hello correlates to the request", res["corr"] == hello_id)
        check("res.hello carries protocolVersion 1",
              res["payload"].get("protocolVersion") == PROTOCOL_VERSION)

        # ── 8. forward compatibility: unknown type must NOT disconnect ───────
        await ws.send(env("cmd.future.feature", {"anything": True}))
        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("unknown type -> err.protocol.unknownType",
              res["type"] == "err.protocol.unknownType", str(res)[:120])

        await ws.send(env("cmd.ping", {}))
        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("connection SURVIVES an unknown type", res["type"] == "res.pong", str(res)[:120])

        # ── 9. malformed envelope ────────────────────────────────────────────
        await ws.send(json.dumps({"v": 1, "id": "not-a-ulid", "ts": now_iso(),
                                  "type": "cmd.ping", "corr": None, "payload": {}}))
        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("malformed envelope -> err.protocol.badEnvelope",
              res["type"] == "err.protocol.badEnvelope", str(res)[:120])

        # ── 10. PTY grant: green for a human in an ordinary directory ────────
        await ws.send(env("cmd.pty.requestSpawn",
                          {"profileId": "cmd", "cwd": "C:\\dev", "actor": "human"}))
        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("human spawn in C:\\dev is granted", res["type"] == "res.pty.grant",
              str(res)[:160])
        check("grant carries grantId + sessionId",
              bool(res["payload"].get("grantId")) and bool(res["payload"].get("sessionId")))

        # ── 11. PTY grant: agent must be confirmed, not auto-granted ─────────
        await ws.send(env("cmd.pty.requestSpawn",
                          {"profileId": "cmd", "cwd": "C:\\dev", "actor": "agent"}))
        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("agent spawn requires approval", res["type"] == "err.permission.pending",
              str(res)[:160])

        # ── 12. protected path: agent shell inside OneDrive ──────────────────
        await ws.send(env("cmd.pty.requestSpawn",
                          {"profileId": "cmd",
                           "cwd": "C:\\Users\\SERIOUS-PC\\OneDrive", "actor": "agent"}))
        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        check("agent shell in protected OneDrive requires approval",
              res["type"] == "err.permission.pending", str(res)[:160])

    print(f"\n{passed} passed, {failed} failed\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
