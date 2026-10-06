"""
core/system/abilities/app.py — open an application by name.

GREEN under `app.launch` (a permissions.yaml key that existed with no tool on
it: app launching lived in the executor's LEGACY `app.open` branch, outside
the registry, invisible to `_validate()` and to the tests that walk it).

THIS REUSES THE 449-APP INDEX. `core.brain.appindex` is the single resolver
and launcher — Start Menu, App Paths, Get-StartApps, the lot, cached to
data/appindex.json — and this capability calls exactly the two functions the
legacy branch calls. It adds nothing to the index and duplicates none of it;
what it adds is a REGISTRY front door with a tier, typed args and an audit
template, so a model-built or typed `system.app.launch` goes through the
same gate as everything else. The router still emits the legacy `app.open`
for voice; retiring that branch onto this entry is a follow-up.

AMBIGUITY IS A REFUSAL, NOT A GUESS. The index returns more than one entry
only when the match is genuinely ambiguous; she names the candidates and
asks, rather than opening the wrong one.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param


def launch(app: str) -> dict[str, Any]:
    from core.brain.appindex import get_index, launch as _launch
    from core.tools.base import ToolError

    entries, how = get_index().resolve(app)
    if not entries:
        raise ToolError(f"I cannot find {app}", "Say the name again, or the name on its icon.")
    if len(entries) > 1:
        names = ", ".join(e.name for e in entries[:3])
        raise ToolError(f"{app} could be {names}", "Which one?")
    entry = entries[0]
    ok, detail = _launch(entry)
    if not ok:
        raise ToolError(f"{entry.name} would not start ({detail})", "Try it again, or name it differently.")
    return {"name": entry.name, "kind": entry.kind, "match": how}


CAPABILITIES = [
    Capability(
        name="system.app.launch", capability="app.launch", tier="green",
        run=launch,
        params=(Param("app", str, doc="The application's name."),),
        phrasings=("open notepad", "launch calculator", "start chrome"),
        success="Opening {name}, Emperor.",
        audit="launch {app}",
        note="core.brain.appindex — the same resolver and launcher as the legacy app.open branch.",
    ),
]
