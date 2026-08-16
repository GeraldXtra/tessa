"""
core/tools/ — the Windows tool surface. Free, local, offline, no model.

WHAT THIS IS FOR

Gerald did not ask for a chatbot. He asked for something that opens browsers,
finds his files, manages his windows, and does what he says on his own machine.
Almost none of that needs a language model, and every part of it that does not
should never pay for one: a folder that opens for ₦0.00 in 40 ms is strictly
better than the same folder opening for ₦0.05 in two seconds, and it keeps
working when the connection does not.

THE REGISTRY IS THE CONTRACT

`REGISTRY` below is the complete list. Each entry carries its tier, the
permissions.yaml capability that governs it, what he might say, and what she
says on success and on failure. Nothing dispatches outside this table.

TIERS ARE NOT DECLARED HERE, THEY ARE CHECKED HERE. permissions.yaml is "THE
SINGLE AUTHORITY on permission tiers" (CONTRACT §6.4). `_validate()` runs at
import and raises if a tool's tier disagrees with that file, or if its
capability is missing from it entirely. A tool that quietly carried its own
tier would be a second authority disagreeing with the first, and the
disagreement would only surface the day it mattered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import (browser, clip, files, procs, shell, sysctl, websearch,
               winman, x_tools)
from .base import TIERS, ToolError, ToolHold, ToolResult, ToolSpec

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "permissions.yaml"


def _spec(*a: Any, **kw: Any) -> ToolSpec:
    return ToolSpec(*a, **kw)


# ─────────────────────────────────────────────────────────────────────────────
# FILES
# ─────────────────────────────────────────────────────────────────────────────

_FILES = [
    _spec(
        name="fs.list", tier="green", capability="fs.list", handler=files.list_dir,
        phrasings=("what's in my downloads", "list my documents",
                   "show me what's in C:\\dev", "what have I got in pictures"),
        success="{things} in {name}, Emperor. {head}.",
        audit="list {path}",
    ),
    _spec(
        name="fs.search", tier="green", capability="fs.search", handler=files.search,
        phrasings=("find a file called invoice", "search for zoey",
                   "where is my resume", "find anything named budget"),
        success="{things}, Emperor. {first}, in {where}.",
        failure="I found none, sir. {reason} {alternative}",
        audit="search {name}",
        note="Metadata only — names, never contents. Never descends a reparse point.",
    ),
    _spec(
        name="fs.read", tier="green", capability="fs.read", handler=files.read_text,
        phrasings=("read me that file", "what's in the readme",
                   "read C:\\dev\\zoey\\plan.md"),
        success="{things}, Emperor. {chars} characters.",
        audit="read {path}",
        note="Refuses a OneDrive placeholder BEFORE reading it — invariant 5.",
    ),
    _spec(
        name="fs.open", tier="green", capability="fs.open", handler=files.open_path,
        phrasings=("open my downloads", "open that file", "downloads"),
        success="Open, Emperor.",
        audit="open {path}",
    ),
    _spec(
        name="fs.reveal", tier="green", capability="fs.reveal", handler=files.reveal,
        phrasings=("show me that in explorer", "reveal it", "where is that file"),
        success="There it is, Emperor.",
        audit="reveal {path}",
    ),
    _spec(
        name="fs.usage", tier="green", capability="fs.usage", handler=files.disk_usage,
        phrasings=("how big is my downloads folder", "how much space is dev using",
                   "size of that folder"),
        success="{name} is {size}, Emperor. Across {things}.",
        audit="usage {path}",
    ),
    _spec(
        name="fs.create", tier="amber", capability="fs.create", handler=files.make_folder,
        phrasings=("make a folder called drafts", "create a new folder in documents"),
        success="Made it, Emperor. {name}.",
        audit="mkdir {path}",
    ),
    _spec(
        name="fs.rename", tier="amber", capability="fs.rename", handler=files.rename,
        phrasings=("rename that to final", "call it invoice march instead"),
        success="Renamed, Emperor. {was} is now {name}.",
        audit="rename {path} -> {to}",
    ),
    _spec(
        name="fs.move", tier="amber", capability="fs.move", handler=files.move,
        phrasings=("move that to documents", "put it in the archive folder"),
        success="Moved, Emperor. {name} is in {to}.",
        audit="move {path} -> {to}",
    ),
    _spec(
        name="fs.copy", tier="amber", capability="fs.copy", handler=files.copy,
        phrasings=("copy that to my desktop", "make a copy in documents"),
        success="Copied, Emperor. {name} is in {to}.",
        audit="copy {path} -> {to}",
    ),
    _spec(
        name="fs.delete", tier="red", capability="fs.delete", handler=files.delete, holds=True,
        phrasings=("delete that file", "get rid of the old folder", "bin it"),
        success="Gone to the Recycle Bin, Emperor. {things}. Say restore if I was wrong.",
        audit="RECYCLE {path}",
        note="Recycle Bin only, via SHFileOperationW + FOF_ALLOWUNDO. No hard-delete path exists.",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# WINDOWS
# ─────────────────────────────────────────────────────────────────────────────

_WINDOWS = [
    _spec(
        name="win.list", tier="green", capability="window.query", handler=winman.list_windows,
        phrasings=("what have I got open", "list my windows", "what's open"),
        success="{n} windows, Emperor. {head}.",
        audit="list windows",
    ),
    _spec(
        name="win.focus", tier="green", capability="window.control", handler=winman.focus,
        phrasings=("bring chrome forward", "switch to vs code", "focus my browser"),
        success="There it is, Emperor.",
        audit="focus {name}",
    ),
    _spec(
        name="win.minimise", tier="green", capability="window.control", handler=winman.minimise,
        phrasings=("minimise chrome", "get that out of the way", "hide vs code"),
        success="Out of the way, Emperor.",
        audit="minimise {name}",
    ),
    _spec(
        name="win.maximise", tier="green", capability="window.control", handler=winman.maximise,
        phrasings=("maximise chrome", "make vs code full screen", "blow that up"),
        success="Full screen, Emperor.",
        audit="maximise {name}",
    ),
    _spec(
        name="win.close", tier="green", capability="window.control", handler=winman.close,
        phrasings=("close chrome", "shut that window", "close notepad"),
        success="{verdict}",
        audit="close {name}",
        note="WM_CLOSE and then VERIFIED. If the app put up a save prompt she says so.",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# PROCESSES
# ─────────────────────────────────────────────────────────────────────────────

_PROCS = [
    _spec(
        name="proc.list", tier="green", capability="system.status", handler=procs.list_processes,
        phrasings=("what's running", "list processes", "how many processes"),
        success="{n} processes running, Emperor.",
        audit="list processes",
    ),
    _spec(
        name="proc.top", tier="green", capability="system.status", handler=procs.top,
        phrasings=("what's eating my cpu", "heaviest processes", "what's using the memory",
                   "top processes by cpu"),
        success="Heaviest first, Emperor. {head}.",
        audit="top by {by}",
        note="CPU is sampled over 300 ms. The instant reading is a lifetime average and is useless.",
    ),
    _spec(
        name="proc.find", tier="green", capability="system.status", handler=procs.find,
        phrasings=("find chrome", "is python running", "any node processes"),
        success="{n} of them, Emperor. {head}.",
        failure="None running, sir. {reason} {alternative}",
        audit="find process {name}",
    ),
    _spec(
        name="proc.kill", tier="amber", capability="process.kill", handler=procs.kill, holds=True,
        phrasings=("kill 14284", "end process 7332", "stop that process"),
        success="Ended, Emperor. {name}.",
        audit="KILL pid {pid}",
        note="Integer PID only. There is no name parameter on this tool by design.",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# CLIPBOARD
# ─────────────────────────────────────────────────────────────────────────────

_CLIP = [
    _spec(
        name="clip.read", tier="green", capability="clipboard.read", handler=clip.read,
        phrasings=("what's on my clipboard", "read the clipboard", "what did I copy"),
        success="{words} words, Emperor. It starts: {preview}",
        audit="read clipboard",
        note="Returns `external_text` — UNTRUSTED. Goes through the fence, never straight to a model.",
    ),
    _spec(
        name="clip.write", tier="green", capability="clipboard.write", handler=clip.write,
        phrasings=("copy that", "put that on my clipboard"),
        success="Copied, Emperor.",
        audit="write clipboard ({chars} chars)",
    ),
    _spec(
        name="clip.clear", tier="green", capability="clipboard.write", handler=clip.clear,
        phrasings=("clear my clipboard", "wipe the clipboard"),
        success="Clipboard is empty, Emperor.",
        audit="clear clipboard",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM = [
    _spec(
        name="sys.volume", tier="green", capability="system.control", handler=sysctl.volume,
        phrasings=("turn it up", "volume down", "mute", "louder", "quieter"),
        success="Done, Emperor.",
        audit="volume {direction}",
    ),
    _spec(
        name="sys.media", tier="green", capability="system.control", handler=sysctl.media,
        phrasings=("pause", "play", "next track", "skip this one", "previous"),
        success="Done, Emperor.",
        audit="media {action}",
    ),
    _spec(
        name="sys.brightness", tier="green", capability="system.control", handler=sysctl.brightness,
        phrasings=("brightness", "set brightness to 40", "dim the screen"),
        success="Brightness is {level}, Emperor.",
        audit="brightness {level}",
        note="Probed via WMI. Says so plainly when the panel does not expose it.",
    ),
    _spec(
        name="sys.disk", tier="green", capability="system.status", handler=sysctl.disk,
        phrasings=("how much space have I got", "disk", "how full is my drive"),
        success="{free_gb:.1f} gigabytes free, Emperor. {free_pct:.0f} percent of the drive.",
        audit="disk",
    ),
    _spec(
        name="sys.memory", tier="green", capability="system.status", handler=sysctl.memory,
        phrasings=("how much memory", "ram", "memory free"),
        success="{free_gb:.1f} gigabytes free, Emperor. {used_pct:.0f} percent in use.",
        audit="memory",
    ),
    _spec(
        name="sys.battery", tier="green", capability="system.status", handler=sysctl.battery,
        phrasings=("battery", "how's my battery", "am I plugged in"),
        success="{pct:.0f} percent, Emperor. You are {where}.{left}",
        audit="battery",
    ),
    _spec(
        name="sys.uptime", tier="green", capability="system.status", handler=sysctl.uptime,
        phrasings=("uptime", "how long has this been up", "when did I boot"),
        success="Up {hours} hours and {minutes} minutes, Emperor.",
        audit="uptime",
    ),
    _spec(
        name="sys.network", tier="green", capability="system.status", handler=sysctl.network,
        phrasings=("am I online", "is the internet up", "network status", "have I got a connection"),
        success="You are {state}, Emperor. {n} adapters up.",
        audit="network",
        note="TCP connect to 1.1.1.1:443 — no ICMP, no HTTP, a few hundred metered bytes.",
    ),
    _spec(
        name="sys.ip", tier="green", capability="system.status", handler=sysctl.ip_address,
        phrasings=("what's my ip", "my ip address", "what address am I on"),
        success="{ip}, Emperor. On {nic}.",
        audit="ip",
    ),
    _spec(
        name="sys.wifi", tier="green", capability="system.status", handler=sysctl.wifi_list,
        phrasings=("what wifi networks are there", "list wifi", "scan for wifi"),
        success="{n} networks, Emperor. {head}.",
        audit="wifi scan",
    ),
    _spec(
        name="sys.lock", tier="green", capability="system.control", handler=sysctl.lock,
        phrasings=("lock the machine", "lock my screen", "lock it"),
        success="Locking, Emperor.",
        audit="lock",
    ),
    _spec(
        name="sys.sleep", tier="green", capability="system.control", handler=sysctl.sleep,
        # NOT "go to sleep" — that is how he tells HER to stop listening, and
        # this tool is green, so the collision suspended his laptop with no
        # confirmation. Suspending the machine names the machine.
        phrasings=("sleep the machine", "sleep the computer", "suspend"),
        success="Sleeping, Emperor.",
        audit="sleep",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# SHELL — the one string-taking tool in the codebase
# ─────────────────────────────────────────────────────────────────────────────

_SHELL = [
    _spec(
        name="shell.execute", tier="red", capability="shell.execute",
        handler=shell.execute, holds=True,
        phrasings=("run git status", "run npm install", "execute dir /s"),
        success="Exit code {code}, Emperor. {lines} lines back.",
        audit="SHELL {command}",
        note="provenance must be 'human'. A model- or page-authored command is refused "
             "unconditionally — no tier, approval or confirmation reaches past it.",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER — everything it returns is UNTRUSTED and is fenced by the executor
# ─────────────────────────────────────────────────────────────────────────────

_BROWSER = [
    _spec(
        name="context.forget", tier="green", capability="system.status",
        handler=lambda: {},
        phrasings=("forget the page", "clear the page", "drop that page",
                   "forget what you read"),
        success="Forgotten, Emperor. My hands are free again.",
        audit="clear external context",
        note="THE WAY OUT of the amber/red block. Reading a page sets "
             "`external_content_in_context`, which gates every amber and red tool. "
             "This is his explicit act to clear it, and it is audited — see "
             "Executor._dispatch_registry.",
    ),
    _spec(
        name="browser.open_url", tier="green", capability="browser.open_url",
        handler=browser.open_url,
        phrasings=("open github dot com", "go to bbc.co.uk", "open x.com in the browser"),
        success="Open, Emperor. {title}.",
        audit="browse {url}",
        note="Launches Chrome LAZILY on first use, in Zoey's own profile — never his.",
    ),
    _spec(
        name="web.search", tier="green", capability="browser.search",
        handler=websearch.search,
        phrasings=("what's the weather", "what's the naira rate",
                   "what's in the news"),
        success="{lead}",
        failure="I could not look that up, sir. {reason} {alternative}",
        audit="web search {query}",
        note="urllib against DuckDuckGo's HTML endpoint. NO browser — one HTTP "
             "request instead of 1.5 s and 560 MB of Chrome. Output is fenced.",
    ),
    _spec(
        name="browser.search", tier="green", capability="browser.search",
        handler=browser.search,
        phrasings=("search for piper tts", "look up the ctranslate2 docs",
                   "google how to disable defender"),
        success="{n} results, Emperor. {head}.",
        failure="I could not search, sir. {reason} {alternative}",
        audit="search web {query}",
        note="DuckDuckGo HTML endpoint. If it blocks or shows a CAPTCHA she says so and STOPS.",
    ),
    _spec(
        name="browser.read_page", tier="green", capability="browser.read",
        handler=browser.read_page,
        phrasings=("read me this page", "what does this page say", "read that article"),
        success="{chars} characters, Emperor. {names} clickable things on it.",
        audit="read page {url}",
        note="Harvests visible text, HIDDEN elements, alt/title/aria attributes and the "
             "accessible names of every clickable element — all fenced as one unit.",
    ),
    _spec(
        name="browser.screenshot", tier="green", capability="browser.screenshot",
        handler=browser.screenshot,
        phrasings=("take a screenshot", "grab a picture of this page"),
        success="Saved it, Emperor. {name}.",
        audit="screenshot {url}",
    ),
    _spec(
        name="browser.close", tier="green", capability="browser.close",
        handler=browser.close_browser,
        phrasings=("close the browser", "shut the browser down", "you can close chrome"),
        success="Browser closed, Emperor.",
        audit="close browser ({reason})",
    ),
    _spec(
        name="browser.click", tier="amber", capability="browser.interact",
        handler=browser.click,
        phrasings=("click accept", "click the sign in button", "press continue"),
        success="Clicked {what}, Emperor.",
        audit="click {name}",
        note="Exactly-one match or it refuses. Never clicks the nearest thing.",
    ),
    _spec(
        name="browser.type", tier="amber", capability="browser.interact",
        handler=browser.type_text,
        phrasings=("type my email in the address field", "put hello in the search box"),
        success="Typed it, Emperor. {chars} characters.",
        audit="type into {field}",
    ),
    _spec(
        name="browser.submit", tier="red", capability="browser.form_submit",
        handler=browser.submit, holds=True,
        phrasings=("submit the form", "send that form"),
        success="Submitted, Emperor.",
        audit="SUBMIT form on {url}",
        note="RED (spec §7.2). Gated on the approval surface: executes ONLY via cmd.permission.respond.",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# X — drives an already-authenticated session. No password ever touches this.
# ─────────────────────────────────────────────────────────────────────────────

_X = [
    _spec(
        name="x.login", tier="green", capability="browser.open_url",
        handler=x_tools.open_for_login,
        phrasings=("open x so I can log in", "sign me into x", "let me log into twitter"),
        success="X is open, Emperor. Sign in yourself — I never see it.",
        audit="open x login",
    ),
    _spec(
        name="x.read_timeline", tier="green", capability="x.read",
        handler=x_tools.read_timeline,
        phrasings=("read my timeline", "what's on x", "what's happening on twitter"),
        success="{n} posts, Emperor. {head}.",
        failure="I could not read your timeline, sir. {reason} {alternative}",
        audit="read x timeline",
    ),
    _spec(
        name="x.read_notifications", tier="green", capability="x.read",
        handler=x_tools.read_notifications,
        phrasings=("any notifications on x", "check my x notifications", "who replied to me"),
        success="{n} of them, Emperor. {head}.",
        failure="I could not read your notifications, sir. {reason} {alternative}",
        audit="read x notifications",
    ),
    _spec(
        name="x.like", tier="amber", capability="x.interact",
        handler=x_tools.like, holds=True,
        phrasings=("like that one", "like post two", "like the first one"),
        success="Liked {who}'s post, Emperor.",
        failure="I did not like it, sir. {reason} {alternative}",
        audit="LIKE x post {index}",
    ),
    _spec(
        name="x.repost", tier="amber", capability="x.interact",
        handler=x_tools.repost, holds=True,
        phrasings=("repost that", "retweet post three", "share the second one"),
        success="Reposted {who}, Emperor.",
        failure="I did not repost it, sir. {reason} {alternative}",
        audit="REPOST x post {index}",
    ),
    _spec(
        name="x.post", tier="red", capability="x.publish",
        handler=x_tools.post, holds=True,
        phrasings=("tweet that", "post this to x", "put that on twitter"),
        success="Posted, Emperor. {chars} characters.",
        audit="POST to x: {text}",
        note="RED. Executes ONLY via cmd.permission.respond, never from voice.",
    ),
    _spec(
        name="x.reply", tier="red", capability="x.publish",
        handler=x_tools.reply, holds=True,
        phrasings=("reply to that", "answer post two", "respond to the first one"),
        success="Replied, Emperor.",
        audit="REPLY to x post {index}: {text}",
        note="RED. Executes ONLY via cmd.permission.respond, never from voice.",
    ),
]


REGISTRY: dict[str, ToolSpec] = {
    s.name: s for s in (_FILES + _WINDOWS + _PROCS + _CLIP + _SYSTEM + _SHELL
                        + _BROWSER + _X)
}


def _validate() -> None:
    """Every tool's tier must match permissions.yaml, or the daemon does not import."""
    raw = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
    tier_of: dict[str, str] = {}
    for tier in TIERS:
        for cap in (raw.get("tiers", {}).get(tier) or []):
            tier_of[cap] = tier

    problems: list[str] = []
    for spec in REGISTRY.values():
        actual = tier_of.get(spec.capability)
        if actual is None:
            problems.append(f"{spec.name}: capability {spec.capability!r} is not in permissions.yaml")
        elif actual != spec.tier:
            problems.append(
                f"{spec.name}: declares tier {spec.tier!r} but permissions.yaml says {actual!r}")
        if spec.tier == "red" and not spec.holds:
            problems.append(f"{spec.name}: RED tools must hold for a second confirmation")
    if problems:
        raise RuntimeError("core/tools registry disagrees with permissions.yaml:\n  "
                           + "\n  ".join(problems))


_validate()


def tier_of(name: str) -> str:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"{name} is not a tool I have", "Ask me something else.")
    return spec.tier


__all__ = ["REGISTRY", "ToolError", "ToolHold", "ToolResult", "ToolSpec", "tier_of"]
