from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.exam import items as I
from core.exam import rehearsal as R
from core.exam import score as S
from core.exam import selftest

SMOKE = ("V01", "R-fs.list", "R-system.volume.set", "C01", "R-x.follow", "S01", "G03", "A01",
         "R-system.software.install", "R-x.post")


def measure_label() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        import psutil
        out["cpu_pct_1s"] = psutil.cpu_percent(interval=1.0)
        out["free_mb"] = round(psutil.virtual_memory().available / 1048576)
    except Exception as exc:
        out["cpu_error"] = str(exc)

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            out["idle_s"] = round((ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0, 1)
    except Exception as exc:
        out["idle_error"] = str(exc)
    idle = out.get("idle_s")
    cpu = out.get("cpu_pct_1s")
    if idle is not None and idle < 60:
        out["label"] = "IN USE"
    elif cpu is not None and cpu > 50:
        out["label"] = "UNDER LOAD"
    else:
        out["label"] = "QUIET"
    return out


def pick(args: argparse.Namespace) -> list[dict[str, Any]]:
    items = I.all_items(include_his=not args.no_his)
    if args.smoke:
        items = [i for i in items if i["id"] in SMOKE]
    if args.area:
        items = [i for i in items if i["area"] in set(args.area)]
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        missing = sorted(want - {i["id"] for i in items})
        if missing:
            raise SystemExit(f"unknown exam ids: {missing}")
        items = [i for i in items if i["id"] in want]
    items = I.ordered(items)
    if args.limit:
        items = items[: args.limit]
    return items


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    finally:
        env = R.ENV
        if env is not None and env.stores_dir.exists():
            import shutil
            shutil.rmtree(env.stores_dir, ignore_errors=True)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.exam",
                                 description="Her exam: rehearse requests through her real brain, run nothing, score.")
    ap.add_argument("--budget", type=int, default=0, help="most Gemini calls this run may make (0 = none)")
    ap.add_argument("--intent-budget", type=int, default=None,
                    help="most calls on her intent model; a call past it is not made and the item is NEEDS MODEL")
    ap.add_argument("--chat-budget", type=int, default=None,
                    help="most calls on her chat model; a call past it is not made and the item is NEEDS MODEL")
    ap.add_argument("--area", action="append", help=f"only this area (repeatable): {', '.join(I.AREAS)}")
    ap.add_argument("--ids", help="only these ids, comma separated")
    ap.add_argument("--smoke", action="store_true", help=f"the smoke set: {', '.join(SMOKE)}")
    ap.add_argument("--repeat", action="store_true", help="re-run the fixed repeat sample after the pass")
    ap.add_argument("--no-his", action="store_true", help="leave out his real requests")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--label", default="", help="override the measured QUIET / UNDER LOAD / IN USE label")
    ap.add_argument("--gap", type=float, default=6.5, help="seconds between two calls to the same model")
    ap.add_argument("--selftest-only", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--rescore", default="", help="re-judge a finished run's saved records with today's items and "
                                                  "scorer; no model call, nothing rehearsed again")
    ap.add_argument("--rescore-as", default="", help="with --rescore: write rescored-NAME.json and "
                                                     "summary-rescored-NAME.md, keeping earlier rescores")
    ap.add_argument("--route-only", action="store_true",
                    help="route every chosen request through her real router, fenced, run nothing, call no model")
    ap.add_argument("--compare", default="", help="with --route-only: an earlier routes.json to list the moves against")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.rescore:
        return rescore(Path(args.rescore), args.rescore_as)
    if args.route_only:
        return route_only(args)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = "selftest" if args.selftest_only else "smoke" if args.smoke else "run"
    out_dir = Path(args.out) if args.out else R.EXAM_DIR / "runs" / f"{stamp}-{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    label = measure_label()
    if args.label:
        label["label"] = args.label
        label["label_overridden"] = True
    items = [] if args.selftest_only else pick(args)
    per_model = args.intent_budget is not None or args.chat_budget is not None
    roles = {"intent": max(0, args.intent_budget or 0), "chat": max(0, args.chat_budget or 0)} if per_model else None
    budget = args.budget if (args.budget > 0 or not per_model) else sum(roles.values())
    before = R.snapshot()
    print(f"exam {stamp}: {len(items)} items, budget {budget} Gemini calls"
          + (f" (intent {roles['intent']}, chat {roles['chat']})" if per_model else "")
          + f", label {label.get('label')} "
          f"(cpu {label.get('cpu_pct_1s')}% over 1 s, his idle {label.get('idle_s')} s) -> {out_dir}", flush=True)
    t0 = time.perf_counter()
    env = R.Rehearsal(out_dir, budget=budget, gap_model_s=args.gap, role_budgets=roles).build()
    build_ms = (time.perf_counter() - t0) * 1000
    st = selftest.run(env)
    write_json(out_dir / "selftest.json", st)
    for c in st["checks"]:
        print(("  self-test PASS " if c["ok"] else "  self-test FAIL ") + c["check"], flush=True)
    meta: dict[str, Any] = {"stamp": stamp, "label": label, "budget": budget, "role_budgets": roles,
                            "model_budgets": env.model_budgets, "build_ms": round(build_ms, 1),
                            "selftest_ok": st["ok"], "selftest_ms": st["ms"], "argv": sys.argv[1:],
                            "models": {"chat": getattr(getattr(env.brain, "primary", env.brain), "name", "?"),
                                       "intent": "settings brain.intent.model"},
                            "gap_model_s": env.gap_model_s, "gap_any_s": env.gap_any_s,
                            "clock": "time.perf_counter (QueryPerformanceCounter); no display is involved"}
    if not st["ok"]:
        meta["refused"] = "the self-test failed: the run did not start and made no model call"
        write_json(out_dir / "results.json", {"meta": meta})
        print("REFUSED: the self-test failed. Nothing ran. See selftest.json.", flush=True)
        return 2
    if args.selftest_only:
        meta["after"] = R.compare(before)
        write_json(out_dir / "results.json", {"meta": meta})
        return 0
    recs: dict[str, dict[str, Any]] = {}
    verdicts: list[dict[str, Any]] = []
    stop_reason = None
    unrun: list[str] = []
    jsonl = out_dir / "results.jsonl"
    for n, item in enumerate(items, 1):
        if stop_reason is not None:
            unrun.append(item["id"])
            continue
        if not per_model and budget - env.calls_used < 2 and budget > 0 and env.calls_used > 0:
            stop_reason = f"budget: {env.calls_used} of {budget} calls used, fewer than 2 left"
            unrun.append(item["id"])
            continue
        rec = env.run_item(item)
        v = S.score_item(item, rec)
        recs[item["id"]] = rec
        verdicts.append(v)
        with open(jsonl, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"item": item, "rec": rec, "verdict": v}, ensure_ascii=False, default=str) + "\n")
        print(f"  [{n:3d}/{len(items)}] {item['id']:<34} {v['verdict']:<6} calls {env.calls_used:3d}/{budget} "
              f"{(v.get('ms') or 0):8.0f} ms  {','.join(v.get('failed') or [])}", flush=True)
        if v["verdict"] in ("UNRUN", "BREACH"):
            stop_reason = v.get("reason")
            if v["verdict"] == "BREACH":
                break
    movement = None
    second: list[dict[str, Any]] = []
    if args.repeat and stop_reason is None:
        again = [i for i in items if i["id"] in I.REPEAT_SAMPLE and i["id"] in recs]
        for item in again:
            if not per_model and budget - env.calls_used < 2 and budget > 0:
                stop_reason = "budget during the repeat sample"
                break
            rec = env.run_item(item)
            v = S.score_item(item, rec)
            second.append(v)
            with open(out_dir / "repeat.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"item": item, "rec": rec, "verdict": v}, ensure_ascii=False, default=str) + "\n")
            print(f"  [repeat] {item['id']:<34} {v['verdict']:<6} calls {env.calls_used:3d}/{budget}", flush=True)
            if v["verdict"] in ("UNRUN", "BREACH"):
                stop_reason = v.get("reason")
                break
        movement = S.movement([v for v in verdicts if v["id"] in {s["id"] for s in second}], second)
    summary = S.summarise(items, recs, verdicts)
    meta.update({"calls_used": env.calls_used, "calls_by_model": dict(env.calls_by_model),
                 "skipped_calls": list(env.skipped_calls), "stop_reason": stop_reason, "unrun": unrun,
                 "wall_s": round(time.time() - started, 1), "breaches": R.REC.breaches,
                 "after": R.compare(before), "stores": env.store_notes,
                 "model_calls": env.model_calls})
    write_json(out_dir / "results.json", {"meta": meta, "summary": summary, "movement": movement,
                                         "verdicts": verdicts, "repeat_verdicts": second})
    (out_dir / "summary.md").write_text(render(meta, summary, verdicts, movement), encoding="utf-8")
    print(render(meta, summary, verdicts, movement), flush=True)
    return 0


def route_only(args: argparse.Namespace) -> int:
    from core.exam import routes as RT

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else R.EXAM_DIR / "runs" / f"{stamp}-route"
    items = pick(args)
    try:
        prev = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else None
    except (OSError, ValueError) as exc:
        print(f"--compare wants a routes.json file: {exc}", flush=True)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    label = measure_label()
    if args.label:
        label["label"] = args.label
        label["label_overridden"] = True
    before = R.snapshot()
    env = R.Rehearsal(out_dir, budget=0, role_budgets={"intent": 0, "chat": 0}).build()
    down = RT.fences_down(env)
    meta: dict[str, Any] = {"stamp": stamp, "mode": "route-only", "label": label, "items": len(items),
                            "fences_down": down, "compare": args.compare or None,
                            "clock": "time.perf_counter (QueryPerformanceCounter); no display is involved"}
    print(f"route-only {stamp}: {len(items)} items, label {label.get('label')} -> {out_dir}", flush=True)
    if down:
        meta["refused"] = "a fence is down: nothing was routed"
        write_json(out_dir / "results.json", {"meta": meta})
        print("REFUSED: a fence is down. Nothing was routed.", flush=True)
        for d in down:
            print(f"  {d['fence']}: {d['why']}", flush=True)
        return 2
    t0 = time.perf_counter()
    rows = RT.route_items(env, items)
    moved = RT.compare(prev.get("rows") or [], rows) if prev is not None else None
    meta.update({"ms": round((time.perf_counter() - t0) * 1000, 1), "model_calls": env.calls_used,
                 "breaches": R.REC.breaches, "after": R.compare(before)})
    RT.write(out_dir, rows, meta, moved, lambda s: print(s, flush=True))
    changed = [r for r in meta["after"] if r["verdict"] not in ("byte-identical", "absent, still absent", "appended")]
    print(f"model calls {env.calls_used}; breaches {len(R.REC.breaches)}; her live files changed: {len(changed)}",
          flush=True)
    return 0


def rescore(run_dir: Path, as_name: str = "") -> int:
    suffix = f"-{as_name}" if as_name else ""
    current = {i["id"]: i for i in I.all_items(include_his=True)}
    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    recs: dict[str, dict[str, Any]] = {}
    verdicts: list[dict[str, Any]] = []
    changed = []
    items = []
    for r in rows:
        item = current.get(r["item"]["id"], r["item"])
        items.append(item)
        v = S.score_item(item, r["rec"])
        recs[item["id"]] = r["rec"]
        verdicts.append(v)
        old = r["verdict"]
        if old.get("verdict") != v.get("verdict") or sorted(old.get("failed") or []) != sorted(v.get("failed") or []):
            changed.append({"id": item["id"], "was": old.get("verdict"), "was_failed": old.get("failed"),
                            "now": v.get("verdict"), "now_failed": v.get("failed")})
    second: list[dict[str, Any]] = []
    rep = run_dir / "repeat.jsonl"
    if rep.exists():
        for line in rep.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                second.append(S.score_item(current.get(r["item"]["id"], r["item"]), r["rec"]))
    movement = S.movement([v for v in verdicts if v["id"] in {s["id"] for s in second}], second) if second else None
    summary = S.summarise(items, recs, verdicts)
    old = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    meta = dict(old.get("meta") or {})
    meta["rescored_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(run_dir / f"rescored{suffix}.json", {"meta": meta, "summary": summary, "movement": movement,
                                                   "verdicts": verdicts, "changed": changed})
    text = render(meta, summary, verdicts, movement)
    text += "\n## Changed by the rescore\n\n" + "\n".join(
        f"- {c['id']}: {c['was']} {c['was_failed'] or ''} -> {c['now']} {c['now_failed'] or ''}" for c in changed) + "\n"
    (run_dir / f"summary-rescored{suffix}.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    return 0


def render(meta: dict[str, Any], summary: dict[str, Any], verdicts: list[dict[str, Any]], movement: Any) -> str:
    L = [f"# Exam run {meta['stamp']} ({meta['label'].get('label')})", ""]
    L.append(f"- label measured: cpu {meta['label'].get('cpu_pct_1s')}% over 1 s, his idle {meta['label'].get('idle_s')} s, "
             f"free {meta['label'].get('free_mb')} MB")
    L.append(f"- Gemini calls: {meta['calls_used']} of a budget of {meta['budget']}; wall {meta['wall_s']} s; "
             f"stop: {meta['stop_reason'] or 'none, every chosen item ran'}")
    if meta.get("model_budgets") is not None:
        used = meta.get("calls_by_model") or {}
        L.append("- per model: " + ", ".join(f"{m} {used.get(m, 0)} of {b}" for m, b in meta["model_budgets"].items())
                 + f"; calls not made (NEEDS MODEL): {len(meta.get('skipped_calls') or [])}")
    if meta.get("unrun"):
        L.append(f"- unrun ({len(meta['unrun'])}): {', '.join(meta['unrun'])}")
    L.append(f"- self-test: {'PASS' if meta['selftest_ok'] else 'FAIL'} in {meta['selftest_ms']} ms; "
             f"breaches during the run: {len(meta['breaches'])}")
    t = summary["totals"]
    L += ["", f"## Totals: {summary['items']} items, {t.get('scored')} scored",
          f"PASS {t.get('PASS', 0)}, LOOK {t.get('LOOK', 0)}, FAIL {t.get('FAIL', 0)}, HALT {t.get('HALT', 0)}, "
          f"UNRUN {t.get('UNRUN', 0)}, NEEDS MODEL {t.get('NEEDS MODEL', 0)}, BREACH {t.get('BREACH', 0)}; "
          f"pass rate {t.get('pass_rate')}, "
          f"pass-or-look {t.get('pass_or_look_rate')}", "", "## By area", "",
          "| area | scored | PASS | LOOK | FAIL | HALT | pass rate |", "|---|---|---|---|---|---|---|"]
    for a, d in summary["by_area"].items():
        L.append(f"| {a} | {d['scored']} | {d.get('PASS', 0)} | {d.get('LOOK', 0)} | {d.get('FAIL', 0)} | "
                 f"{d.get('HALT', 0)} | {d.get('pass_rate')} |")
    L += ["", "## By failure type", ""]
    for k, n in summary["by_failure"].items():
        L.append(f"- {k}: {n}")
    L += ["", "## Time per stage (ms, time.perf_counter, net of the exam's own pacing waits)", "",
          "| stage | n | p50 | p95 |", "|---|---|---|---|"]
    for s, d in summary["stage_ms"].items():
        L.append(f"| {s} | {d['n']} | {d['p50']} | {d['p95']} |")
    d = summary["turn_total_ms"]
    L.append(f"| whole turn | {d['n']} | {d['p50']} | {d['p95']} |")
    m = summary["model_calls_per_item"]
    L += ["", f"## Model calls per item: mean {m['mean']}, max {m['max']}, distribution {m['distribution']}"]
    for p, d in summary["model_call_ms"].items():
        L.append(f"- {p} call ms: n {d['n']}, p50 {d['p50']}, p95 {d['p95']}; first token "
                 f"p50 {summary['model_ttft_ms'].get(p, {}).get('p50')}")
    if movement:
        L += ["", f"## Repeat sample: {movement['pairs']} pairs, {movement['verdict_flips']} verdicts moved, "
                  f"dimension flips {movement['dimension_flips']}"]
        for r in movement["rows"]:
            L.append(f"- {r['id']}: {r['first']} -> {r['second']}; tools {r['chosen_first']} -> {r['chosen_second']}"
                     + (f"; moved {r['dims_moved']}" if r["dims_moved"] else ""))
    L += ["", "## Her live files after the run", ""]
    for r in meta["after"]:
        L.append(f"- {r['verdict']}: {r['path']}" + (f" (+{r['grew']} bytes, hers)" if r.get("grew") else ""))
    L += ["", "## Every item", "", "| id | area | verdict | failed | chose | ms | calls |", "|---|---|---|---|---|---|---|"]
    for v in verdicts:
        L.append(f"| {v['id']} | {v['area']} | {v['verdict']} | {', '.join(v.get('failed') or [])} | "
                 f"{', '.join(c['name'] for c in v.get('chosen') or [])} | {round(v.get('ms') or 0)} | "
                 f"{v.get('model_calls', 0)} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
