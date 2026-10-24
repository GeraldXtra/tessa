from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from core.exam import rehearsal as R

INDEX_HOOKS = {"appindex": ("build_fast", "build_slow", "build_async", "_save"),
               "fileindex": ("build_fast", "build_full", "build_async", "_save")}


def fences_down(env: R.Rehearsal) -> list[dict[str, Any]]:
    from core.brain import appindex
    from core.exam import selftest
    from core.system.abilities import service_control, software_change
    from core.system.winapi import fileindex

    down: list[dict[str, Any]] = []
    if not (env.built and env.fenced):
        down.append({"fence": "environment", "why": "not built and fenced"})
    R.REC.expecting = True
    try:
        for label, fn, want in selftest._probes(env):
            try:
                out = fn()
                try:
                    if hasattr(out, "close"):
                        out.close()
                except Exception:
                    pass
                down.append({"fence": label, "why": "returned instead of being refused"})
            except want:
                continue
            except BaseException as exc:
                down.append({"fence": label, "why": f"{type(exc).__name__}: {str(exc)[:120]}"})
    finally:
        R.REC.expecting = False
        R.REC.take()
    for mod, attr in ((software_change, "resolve"), (service_control, "resolve"), (service_control, "table")):
        if getattr(getattr(mod, attr), "__module__", "") != R.__name__:
            down.append({"fence": f"{mod.__name__}.{attr}", "why": "routing would run the real lookup"})
    stores = R._norm_roots([env.stores_dir])
    for name, idx in (("appindex", appindex._INDEX), ("fileindex", fileindex._INDEX)):
        if idx is None:
            down.append({"fence": name, "why": "no copy installed"})
            continue
        if not R._under(str(getattr(idx, "cache_path", "")), stores):
            down.append({"fence": name, "why": f"cache is {getattr(idx, 'cache_path', None)}, not a copy"})
        live = [h for h in INDEX_HOOKS[name] if h not in vars(idx)]
        if live:
            down.append({"fence": name, "why": f"{', '.join(live)} would build or save for real"})
    return down


def kind_of(row: dict[str, Any]) -> str:
    if row.get("halt"):
        return f"halt:{row['halt'].get('kind')}"
    if row.get("stop") or row.get("error"):
        return "error"
    calls = row.get("calls") or []
    if calls:
        return "tool:" + ",".join(str(c.get("name")) for c in calls)
    if row.get("plan"):
        return "plan"
    intent = str(row.get("intent") or "")
    if intent == "tool" and row.get("speech"):
        return "ask"
    return f"intent:{intent}" if intent else "unrouted"


def route_items(env: R.Rehearsal, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from core.brain.conversation import Conversation
    from core.brain.router import Router, history_kwargs

    rows: list[dict[str, Any]] = []
    for item in items:
        conv = Conversation(path=env.stores_dir / "conversation-route.json")
        conv.save = lambda: R.REC.note("store", store="conversation", write="skipped")
        for role, text in item.get("before") or []:
            conv.add(role, text)

        def status() -> dict[str, Any]:
            raise R.RoutingHalt("status", "her live health")

        router = Router(health_sample=status)
        env.current_item = str(item.get("id"))
        for n, text in enumerate(item.get("turns") or []):
            row: dict[str, Any] = {"id": item.get("id"), "area": item.get("area"), "source": item.get("source"),
                                   "turn": n}
            if item.get("source") != "his":
                row["text"] = str(text)
            t0 = time.perf_counter()
            try:
                row.update(R.routed_info(router.route(str(text), **history_kwargs(router, conv))) or {})
            except R.RoutingHalt as h:
                row["halt"] = {"kind": h.kind, **h.detail}
            except R.Halt as h:
                row["stop"] = {"kind": h.kind, "class": type(h).__name__}
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            row["ms"] = round((time.perf_counter() - t0) * 1000, 3)
            row["kind"] = kind_of(row)
            rows.append(row)
        env.current_item = None
    return rows


def _sig(row: dict[str, Any]) -> str:
    return json.dumps({"kind": row.get("kind"), "calls": row.get("calls"), "speech": row.get("speech")
                       if row.get("kind") == "ask" else None}, sort_keys=True, default=str)


def compare(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old = {(r["id"], r["turn"]): r for r in before}
    moved = []
    for r in after:
        b = old.get((r["id"], r["turn"]))
        if b is None:
            moved.append({"id": r["id"], "turn": r["turn"], "source": r.get("source"), "before": None,
                          "after": r.get("kind")})
        elif _sig(b) != _sig(r):
            moved.append({"id": r["id"], "turn": r["turn"], "source": r.get("source"), "before": b.get("kind"),
                          "after": r.get("kind")})
    return moved


def line(row: dict[str, Any]) -> str:
    if row.get("source") == "his":
        return f"  {row['id']}#{row['turn']}: {str(row.get('kind', '')).split(':', 1)[0]}"
    calls = row.get("calls") or []
    what = "; ".join(f"{c.get('name')} {json.dumps(c.get('args'), default=str)[:160]}" for c in calls)
    return f"  {row['id']}#{row['turn']}: {row.get('kind')}" + (f"  {what}" if what else "") + (
        f"  \"{str(row.get('speech'))[:120]}\"" if row.get("kind") == "ask" else "")


def moved_line(m: dict[str, Any]) -> str:
    if m.get("source") == "his":
        return f"  {m['id']}#{m['turn']}: moved"
    return f"  {m['id']}#{m['turn']}: {m['before']} -> {m['after']}"


def write(out_dir: Path, rows: list[dict[str, Any]], meta: dict[str, Any],
          moved: list[dict[str, Any]] | None, show: Callable[[str], None]) -> None:
    (out_dir / "routes.json").write_text(json.dumps({"meta": meta, "rows": rows, "moved": moved}, indent=1,
                                                    ensure_ascii=False, default=str), encoding="utf-8")
    for row in rows:
        show(line(row))
    counts: dict[str, int] = {}
    for row in rows:
        k = str(row.get("kind", "")).split(":", 1)[0]
        counts[k] = counts.get(k, 0) + 1
    show(f"routed {len(rows)} turns of {len({r['id'] for r in rows})} items: "
         + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if moved is not None:
        show(f"moved against the earlier snapshot: {len(moved)}")
        for m in moved:
            show(moved_line(m))
