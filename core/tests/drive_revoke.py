"""
core/tests/drive_revoke.py — item 11(e): drive evt.pty.revoke for real.

Takes the sessionId of a LIVE Console PTY and replays `cmd.pty.report{started}`
on it. The daemon refuses the replay (the grant is already redeemed) and answers
with `evt.pty.revoke` for that sessionId — which the Console must honour by
killing the PTY and reporting `killed` back.

That makes this a genuine end-to-end revoke against a real process rather than a
simulated one: the revoke originates in the daemon's own enforcement path, not
from a test hook wired in for the occasion.

    python core/tests/drive_revoke.py <sessionId>
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.security import runtime as rt  # noqa: E402

_A = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    ms = int(time.time() * 1000)
    t = ""
    for _ in range(10):
        t = _A[ms % 32] + t
        ms //= 32
    return t + "".join(secrets.choice(_A) for _ in range(16))


def env(mtype: str, payload: dict) -> str:
    return json.dumps({
        "v": 1, "id": ulid(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "type": mtype, "corr": None, "payload": payload,
    })


async def main(session_id: str) -> int:
    info = rt.read_runtime_file()
    if not info:
        print("no live daemon")
        return 2

    async with websockets.connect(
        f"ws://127.0.0.1:{info['port']}/v1",
        additional_headers={"Origin": "tessa://console"},
        open_timeout=5,
    ) as ws:
        await ws.send(env("cmd.hello", {
            "token": info["token"], "surface": "console",
            "surfaceVersion": "revoke-driver", "protocolVersion": 1,
        }))
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        if hello.get("type") != "res.hello":
            print(f"hello failed: {hello}")
            return 1

        # Absolute stamp, so an external Win32_Process poller and the Console's
        # own "revoke received at ..." line sit on ONE wall clock. Relative ms
        # from three different t0s is how a measurement quietly becomes fiction.
        print(f"driving revoke for session {session_id}")
        print(f"REVOKE-TRIGGER-SENT-AT {datetime.now(timezone.utc).isoformat()}", flush=True)
        await ws.send(env("cmd.pty.report", {"sessionId": session_id, "event": "started"}))

        # Read whatever the daemon says back, including the broadcast revoke.
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                raw = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            except asyncio.TimeoutError:
                break
            t = raw.get("type", "")
            if t in ("err.permission.denied", "evt.pty.revoke"):
                print(f"  {t}: {json.dumps(raw['payload'])}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: drive_revoke.py <sessionId>")
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
