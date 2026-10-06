# PROPOSED CONTRACT DIFF — the command card should show the shell and the cwd

**Status: PROPOSED, NOT APPLIED.** `CONTRACT.md` is Gerald's. This is the diff
this round would make, with the reasoning, for him to accept or reject.

---

## What is already true

`shell.execute` is red-tier and card-gated. The `evt.permission.requested`
payload already carries the literal command, because it carries `args`:

```json
{
  "requestId": "871170cd...",
  "tier": "red",
  "tool": "shell.execute",
  "args": { "command": "npm install" },
  "provenance": "human",
  "expiresAt": "...",
  "frozen": ["command", "cwd", "timeout_s"]
}
```

So he does see the exact string he is approving, and `frozen` tells the surface
to render it read-only. That half is done and proven.

## What is missing

**The card does not say WHERE the command runs, or in WHICH shell.**

Two arguments exist on the tool and are now frozen, but neither appears on the
card because both are left at their defaults and `args` only carries what the
caller supplied:

* `cwd` — defaults to the daemon's working directory. `dir` lists a different
  directory depending on it; `.\build.cmd` resolves to a different file.
* the shell — always `cmd.exe /c` today, which is not stated anywhere he can
  see. If a future change made PowerShell an option, a card that never
  mentioned the shell would silently change meaning.

"Approve `npm install`" is a materially different decision in `C:\dev\tessa`
than in `C:\Users\SERIOUS-PC`. He cannot currently tell which he is agreeing to.

## The proposed change

Additive to the `evt.permission.requested` payload — CONTRACT §7.2 permits an
additive field without a `PROTOCOL_VERSION` bump, which is the same basis on
which `frozen` was added:

```diff
  {
    "requestId": "...",
    "tier": "red",
    "tool": "shell.execute",
    "args": { "command": "npm install" },
    "provenance": "human",
    "expiresAt": "...",
    "frozen": ["command", "cwd", "timeout_s"],
+   // OPTIONAL. Present only for tools that execute in an environment the
+   // owner must see to approve informedly. Absent for every other tool.
+   "runContext": {
+     "shell": "cmd.exe /c",
+     "cwd": "C:\\dev\\tessa",
+     "timeoutS": 120
+   }
  }
```

**Surface rendering:** when `runContext` is present, the card shows the command
prominently and the shell/cwd/timeout as a subordinate line. A surface that
does not understand the field ignores it and loses nothing it has today, which
is what makes this additive rather than breaking.

**Producer:** `core/brain/approvals.py::ApprovalGate.request` would take an
optional `run_context` and include it when set; `core/brain/executor.py` would
populate it for `shell.execute` from the resolved cwd and the tool's own
constants. Roughly fifteen lines, no new gate, no change to what may execute.

## Why it is worth doing

The whole security argument for this tool is *he saw exactly what he approved*.
Today that is true of the command and not of the environment it runs in, and
the environment is part of what a command does. It is a small gap and it is on
the one capability where small gaps are not small.

## Why it is NOT applied here

The round's boundary says to propose a CONTRACT diff rather than apply one, and
this touches a payload both surfaces render. It also wants a matching change in
`apps/orb` and `apps/console`, which belong to other sessions — shipping the
daemon half alone would put a field on the wire that nothing displays, which is
worse than the current honest omission.
