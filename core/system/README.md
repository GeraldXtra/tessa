# core/system — the capability framework

**Session 1. Distinct from `core/capabilities/` (Session 3's credential vault — read-only to us).**

This is the mechanism by which Tessa controls the machine, built so that it scales to
*everything* — including the destructive things — without any of it being able to run
unapproved. The design is in `core/system/__init__.py`; this file is the operational summary.

## The rule

> A capability cannot exist without a tier, and it cannot pick its own.

A capability is a `Capability` (`core/system/capability.py`): name, the `permissions.yaml`
key that governs it, the tier its author expects, typed parameters, and a `run` that returns
facts. `bind()` turns it into an ordinary `ToolSpec` and merges it into `core.tools.REGISTRY`
from `core/tools/__init__.py`. There is no second registry and no second gate.

| Layer | What it does | Where |
|---|---|---|
| bind | asks `core.security.guard.Guard` (permissions.yaml) for the key's tier; module disagrees → `CapabilityError`, daemon does not start; key unlisted → guard answers **red**, `bound_specs()` refuses to register it | `capability.py::bind`, `bound_specs` |
| handler | enforces the tier inside the wrapper: red needs `_approved_by_surface` (only `execute_approved` passes it); amber raises `ToolHold` until `confirmed`, refuses non-human origins; green runs unless the guard's protected-path rule says CONFIRM | `capability.py::_make_handler` |
| registry | `core/tools/_validate()` re-checks tier vs permissions.yaml and red→holds over the merged table | `core/tools/__init__.py` |
| executor | fence → red gate → hold → forgeable-flag strip → run → re-fence → audit with the true actor — unchanged | `core/brain/executor.py` |

The three executor-owned flags (`provenance`, `confirmed`, `_approved_by_surface`) are
stripped from a call's args at dispatch and re-supplied by signature, so a model's args cannot
forge any of them. A capability module may not declare them as parameters (`__post_init__`).

## Adding a capability

1. One module under `core/system/abilities/` exporting `CAPABILITIES` (see `volume.py` for the
   shortest real example, `radio.py` for amber).
2. One line under the right tier in `core/config/permissions.yaml` if the key is new.
3. Restart the daemon. Nothing else — no `server.py` change, no registry edit.

Adding a **red** capability (`files.delete`, `system.shutdown`) is the same two steps with
`tier="red"` and a red key; the worked declarations are in `core/system/__init__.py`. The
executor's red gate raises the approval card for it and `execute_approved` is the only route
that runs it. **`system.power.*` (shut down / restart / hibernate / log off) is the first red
family built this way (2026-09-11)** — see `abilities/power.py`: approval SCHEDULES the action
(60 s default, delay frozen on the card) and `system.power.cancel` aborts it until it fires.

## What is built (2026-09-07)

| Tool | Tier | Key | Mechanism | Works here? |
|---|---|---|---|---|
| `system.volume.get` / `.set` / `.mute` | green | `system.control` | `IAudioEndpointVolume` via stdlib ctypes | yes — exact, ~10 ms, read back |
| `system.brightness.get` / `.set` | green | `system.control` | WMI via `core.tools.sysctl.brightness` (reused) + readback | yes — HD 620 panel exposes it |
| `system.network.status` | green | `system.status` | `netsh wlan` + WinRT radios + TCP handshake (reused) | yes |
| `system.radio.set` | **amber** | `system.radio` (new line) | WinRT `Radio.SetStateAsync` via PowerShell 5.1, constant script | yes — holds for his yes |
| `system.app.launch` | green | `app.launch` | `core.brain.appindex` (the 449-app index, reused) | yes |
| `system.process.details` | green | `system.status` | psutil, one pid or every match of a name; CPU sampled 300 ms (2026-09-11) | yes |
| `system.process.priority` | **amber** | `process.priority` (new line) | psutil `nice()` by pid, **pid frozen**, `describe` names the process before the hold; refuse list shared with `proc.kill` (`core/tools/procs.py::protection`) — pid 0/4, core names, the daemon, its ancestors, its descendants; realtime not offered | yes — holds for his yes |
| `system.power.shutdown` / `.restart` / `.hibernate` / `.logoff` | **red** | `system.shutdown` | approval SCHEDULES an in-process `threading.Timer` (60 s default, 10 s-1 h, **delay frozen** on the card); at fire time `shutdown.exe /s /t 0` / `/r /t 0` / `/h` / `/l` — fixed argv, `shell=False`, **no `/f`** so unsaved work can veto; a daemon restart is an implicit cancel (2026-09-11) | proven with a recorder — never fired for real |
| `system.power.cancel` | green | `system.control` | `timer.cancel()` on the pending action, then `shutdown.exe /a` for a countdown started outside the daemon | yes |
| `system.power.status` | green | `system.status` | what is pending and when; read-only | yes |
| `sys.sleep` / `sys.lock` | green | `system.control` | **reused, not rebuilt** (core/tools/sysctl.py: `SetSuspendState(0,0,0)`, `LockWorkStation`); the power batch only added phrasings ("lock my screen", "put the laptop to sleep") | yes |
| `system.software.search` | green | `system.inventory` | `winget search` (catalog) / `winget list` (installed), `--source winget`, fixed argv, `shell=False`; the **name → exact id** step for the two below. Output fenced; rows whose id is not catalog-shaped (`ARP\`, `MSIX\`) dropped and counted (2026-09-12) | yes — 1.3 s live |
| `system.software.install` | **red** | `software.install` (new line) | ⚠⚠ **the second master key.** `winget install --exact --id <ID> --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity` — a constant argv with ONE slot that admits only a catalog-shaped id (`ID_RE`): no file, no URL, no `--manifest`, no other source can be expressed. **id frozen** on the card; `describe` names "install VLC media player (VideoLAN.VLC)"; refuses without the real threaded `_approved_by_surface` | proven with a recorder — nothing installed |
| `system.software.uninstall` | **red** | `software.uninstall` (new line) | `winget uninstall --exact --id <ID> --source winget`, id frozen. **Protect-list** (`software_change.protection`): the daemon's Python + launcher, Chrome, Git, Node, App Installer, Windows Terminal, OneDrive; VC/.NET/AppRuntime/Xaml/Edge/DirectX/PowerShell prefixes; Intel/NVIDIA/AMD/Realtek drivers — refused BEFORE the card (`describe`) and AFTER approval (`run`) | proven with a recorder — nothing removed |

`proc.kill` (core/tools) was hardened in the same round rather than duplicated here: `frozen=("pid",)`,
the refuse list above, a name+create-time pin across the hold, and a kill by pid handle
(`psutil.Process.kill`) — no `taskkill`, no image name. The `describe` hook on `Capability` is
what lets a pid-addressed amber capability name its target in the hold and refuse a
catastrophic one before asking.

No new Python dependency. `core/system/winapi/` holds the Windows mechanisms: every subprocess
is a fixed argv with `shell=False`; every COM call is a typed vtable slot.

## Deploy

The bound specs are merged at `core.tools` import. **They go live on the next daemon
restart.** No `server.py` hook is required (the registry line is the hook), so there is no
`HOOK-PROPOSAL.md` for this round. `res.hello.capabilities` is a coarse feature list and is
unchanged.

Voice routing (`core/brain/intents.py`) is not touched this round: the new tools are reachable
by the executor (typed/model/approval paths) and by name; adding intent rules is a follow-up
listed in the round report, because every regex added there is a collision risk with the
existing table and belongs in its own proof.

## Proof

`proof-system.py` (session scratchpad) exercises the real framework, guard and executor on a
COPY of the audit chain: registration and tiers, green unattended with real before→after
values, amber holding and refusing non-human origins, an unlisted key bound red and refused
by `bound_specs`, a test-only red stub still gated by the card with a forged
`_approved_by_surface` stripped, and the chain on the copy verifying past the known seq-69
fork. Then the existing suites.
