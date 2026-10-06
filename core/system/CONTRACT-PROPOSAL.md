# CONTRACT-PROPOSAL.md — one additive field on `evt.permission.request`

**Status: PROPOSAL. NOT APPLIED.** `CONTRACT.md` is the owner's file and no session edits it
(CONTRACT §0, THE RULE). This is the diff, with the reasoning, for him to apply or reject.

Written 2026-09-08 by the X round-3 build (Session 1 territory).

---

## What changed in the daemon

`ApprovalGate.request()` now broadcasts one extra key in `evt.permission.request`:

```jsonc
{
  "requestId": "…",
  "tier": "red",
  "tool": "system.x.post_reply",
  "args": { "reply_to_id": "1888…", "text": "…", "source_author": "@colleague", … },
  "provenance": "human",
  "expiresAt": "2026-09-08T02:31:00.000Z",
  "frozen": ["reply_to_id", "source_author", "source_text", "injection_seen"]   // NEW
}
```

`frozen` is the list of argument names the surface must render **read-only**. Everything not
in it stays editable, which is the card's whole purpose.

## Why it exists

`cmd.permission.respond` may carry `editedArgs`, because Whisper mangles dictation and the card
is where he corrects it. That is right for what a red action *says* and wrong for what it is
*aimed at*.

The concrete failure it prevents: he reads a card — "reply to @colleague's post about the audit
log" — approves the wording, and the response frame carries a different `reply_to_id`. His
approved sentence lands under a stranger's post, publicly, permanently, and the card he trusted
is what delivered it.

The daemon already refuses this: `resolve_edit()` raises `protocol.badEnvelope` for any edit to
a frozen key, so **the guarantee does not depend on the surface honouring this field**. `frozen`
exists so the card does not offer him a text box the daemon will reject — a control that looks
editable and is not is a lie about what he can change.

## The diff

```diff
@@ §4.1 Shared events — evt.permission.request @@
-| `evt.permission.request` | `{ requestId, tier, tool, args, provenance, expiresAt }` | `tier` ∈ `green` \| `amber` \| `red`. **`provenance` REQUIRED** — see §6. Either surface may render the approval card. |
+| `evt.permission.request` | `{ requestId, tier, tool, args, provenance, expiresAt, frozen? }` | `tier` ∈ `green` \| `amber` \| `red`. **`provenance` REQUIRED** — see §6. Either surface may render the approval card. **`frozen` is OPTIONAL and additive (§7.2)**: argument names the surface MUST render read-only. Absent or empty means every argument is editable, which is the behaviour every tool had before it existed. The daemon refuses an edit to a frozen argument regardless of what the surface does (`protocol.badEnvelope`), so this field is for the UI's honesty, not for the security boundary. |

@@ §5.1 Shared commands — cmd.permission.respond @@
   `cmd.permission.respond` | `{ requestId, decision, remember? }` …
+  A surface MUST NOT offer an edit control for an argument named in the request's
+  `frozen` list. Sending one is refused with `protocol.badEnvelope` and the request
+  stays pending.
```

## Versioning

**Additive under §7.2 — no `PROTOCOL_VERSION` bump.** A new OPTIONAL field inside a known
payload is exactly what §3.2's forward-compatibility rule covers: a surface that has never heard
of `frozen` ignores it and behaves as it does today. No enum gains a value; `ErrorCode` already
contains `protocol.badEnvelope`.

## What the Orb/Console should do with it

Render those fields as read-only text next to the editable ones. For `system.x.post_reply` that
means the card shows, above the editable reply box:

* who wrote the post being replied to (`source_author`)
* what it said (`source_text`)
* a visible warning when `injection_seen > 0` — that post tried an instruction on her
* the post id being replied to (`reply_to_id`)

The warning matters most. Approving words without seeing what provoked them is the blind spot
the injection fence exists to close, and the card is the last place it can be closed.
