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

from . import (browser, claims_tools, clip, files, procs, shell, sysctl,
               websearch, winman, x_tools)
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
        phrasings=("find a file called invoice", "search for tessa",
                   "where is my resume", "find anything named budget"),
        success="{things}, Emperor. {first}, in {where}.",
        failure="I found none, sir. {reason} {alternative}",
        audit="search {name}",
        note="Metadata only — names, never contents. Never descends a reparse point.",
    ),
    _spec(
        name="fs.read", tier="green", capability="fs.read", handler=files.read_text,
        phrasings=("read me that file", "what's in the readme",
                   "read C:\\dev\\tessa\\plan.md"),
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
        # ⚠⚠ THE TARGET IS FROZEN THROUGH APPROVAL — the hole shell.execute had.
        # With `frozen = ()`, `resolve_edit` MERGED an edited `path` (same key,
        # same type, no rule against it): he approves "delete scratchA" on the
        # card and the frame that comes back carries scratchB, so the wrong file
        # goes to the bin under his approval. A path has no wording to correct —
        # if it was misheard he denies it and says it again. `confirmed` needs
        # no freezing: it is stripped at dispatch and `resolve_edit` refuses any
        # key the request never had.
        frozen=("path",),
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
        # ⚠⚠ THE PID IS FROZEN THROUGH APPROVAL — the same hole `fs.delete` and
        # `shell.execute` had. With `frozen = ()`, `resolve_edit` would MERGE an
        # edited `pid` (same key, same type, no rule against it): he approves
        # "kill 4242" on a card and the frame that comes back carries 999, so a
        # different process dies under his approval. A pid has no wording to
        # correct — if it was misheard he denies it and says it again.
        frozen=("pid",),
        phrasings=("kill 14284", "end process 7332", "stop that process"),
        success="Ended, Emperor. {name}.",
        audit="KILL pid {pid}",
        note="Integer PID only. There is no name parameter on this tool by design. "
             "REFUSES (never holds) pid 0/4, Windows core processes, the daemon itself, "
             "what it runs inside and what runs under it — core/tools/procs.py. "
             "The pid is FROZEN through approval; the kill is by pid handle, never image name.",
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
        # ⚠⚠ EVERY ARGUMENT IS FROZEN, AND THIS TOOL IS WHY THE MECHANISM EXISTS.
        #
        # `frozen` was added for a tweet's `reply_to_id`, on the reasoning that a
        # card exists so the owner can correct what an action SAYS, never what it
        # is AIMED AT. `shell.execute` was written before that field existed and
        # never opted in — so it shipped with `frozen = ()`, which meant
        # `resolve_edit` would MERGE an edited `command`: same key, same type,
        # no rule against it.
        #
        # The concrete failure that permitted: he reads "run: echo hi" on the
        # card, approves it, and the frame that comes back carries
        # `command: "del /s /q C:\\"`. The merge is clean, the executor runs the
        # swapped string, and the card he trusted is what delivered it. Every
        # other safeguard on this tool held — provenance, the fence, the
        # flag-strip — and none of them looks at an approved request's edited
        # args, because by then the approval has already happened.
        #
        # ⚠ A COMMAND HAS NO WORDING TO CORRECT, so unlike a tweet nothing here
        # is editable at all. If it was misheard he denies it and says it again;
        # that costs him five seconds and is the only safe answer. `cwd` and
        # `timeout_s` are frozen for the same reason — cwd decides what a
        # relative command resolves against, which is part of what it DOES.
        frozen=("command", "cwd", "timeout_s"),
        note="provenance must be 'human'. A model- or page-authored command is refused "
             "unconditionally — no tier, approval or confirmation reaches past it. "
             "⚠ EVERY argument is frozen through approval: what he read on the card is "
             "byte-for-byte what runs, and an edited approval is REFUSED rather than "
             "merged. A command is not wording; there is nothing to correct.",
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
        note="Launches Chrome LAZILY on first use, in Tessa's own profile — never his.",
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
    # ── THE READING ROUND (X features round 1, 2026-09-12). Three more GREEN
    #    reads on the same capability key, the same parser and the same fence
    #    as the timeline read. Their output is strangers' text and is fenced
    #    as external; their posts feed the claim and style stores through the
    #    executor exactly as the timeline's do (Executor._X_READ_PREFIXES).
    #    None of them can click: core/tests/test_tools.py checks the AST.
    _spec(
        name="x.search", tier="green", capability="x.read",
        handler=x_tools.search,
        phrasings=("search x for piper tts", "what are people saying about the naira on x",
                   "find tweets about titan wave"),
        success="{n} posts for {query}, Emperor. {head}.",
        failure="I could not search X, sir. {reason} {alternative}",
        audit="search x for {query}",
        note="READ ONLY. Posts search only — people search is deferred until a live session "
             "can verify its selectors. Results are fenced as external and noted only as "
             "unverified claims.",
    ),
    _spec(
        name="x.read_thread", tier="green", capability="x.read",
        handler=x_tools.read_thread,
        phrasings=("read the thread on post two", "what are the replies to that",
                   "read the replies to 1234567890123"),
        success="{n} posts in the thread on {who}'s post, Emperor: {replies} replies. {head}.",
        failure="I could not read that thread, sir. {reason} {alternative}",
        audit="read x thread {post_id}",
        note="READ ONLY. Addressed by status id; 'post two' resolves against the snapshot "
             "of the last read, never a live position.",
    ),
    _spec(
        name="x.read_user", tier="green", capability="x.read",
        handler=x_tools.read_user,
        phrasings=("what has ada been posting", "read ada's profile", "show me ada's posts on x"),
        success="{n} posts from {handle}, Emperor. {head}.",
        failure="I could not read that profile, sir. {reason} {alternative}",
        audit="read x profile {handle}",
        note="READ ONLY. The voice-learning round's reader, now a tool of its own. Fenced "
             "even for his own account: a profile page renders other people's quoted posts.",
    ),
    # ── THE MEDIA ROUND (2026-09-22), part 1: a post's link. GREEN on the
    #    read key and not even a page load — the permalink is what every read
    #    already extracts (built by shape from the status href), resolved
    #    against the last read's snapshot. X's Share button is never pressed.
    _spec(
        name="x.share_link", tier="green", capability="x.read",
        handler=x_tools.share_link,
        phrasings=("share that post", "give me the link to that", "copy that post's link"),
        success="{spoken}",
        failure="I could not get that link, sir. {reason} {alternative}",
        audit="share a post's link",
        note="READ ONLY, no navigation, no press: the permalink from the last read's snapshot "
             "(handle + status id by shape), or X's id-only permalink for a bare id.",
    ),
    # ── THE ENGAGEMENT ROUND (X features round 2, 2026-09-12): like/unlike,
    #    follow/unfollow. AMBER — a like or a follow is a public statement in
    #    his name, and reversible — so each HOLDS for his confirmation rather
    #    than running green or waiting on a card. THE TARGET IS FROZEN: the
    #    handler resolves "post two" to a status id and raises ToolHold(resolved=)
    #    with it, so the ledger is armed on the id (core/brain/executor.py,
    #    step 3b), his "yes" re-runs the id, and `frozen` below means a card —
    #    if one is ever raised for an amber tool — may not edit the target
    #    (resolve_edit). ONE target per call: the handler refuses anything that
    #    is not exactly one id / one @name BEFORE any navigation, so "like
    #    everything" and "follow all my followers" cannot be expressed. Every
    #    press is `x_tools._press`, the one action boundary; the reverse of
    #    each act is the next spec down.
    _spec(
        name="x.like", tier="amber", capability="x.interact",
        handler=x_tools.like, holds=True,
        frozen=("post_id",),
        phrasings=("like that one", "like post two", "like the first one",
                   "like 1234567890123"),
        success="Liked {who}'s post {post_id}, Emperor.{note}",
        failure="I did not like it, sir. {reason} {alternative}",
        audit="LIKE x post {post_id}",
        note="By STATUS ID. An ordinal resolves against the last read's snapshot once, at "
             "request time; the hold is armed on the id. Idempotent. Reverse: x.unlike.",
    ),
    _spec(
        name="x.unlike", tier="amber", capability="x.interact",
        handler=x_tools.unlike, holds=True,
        frozen=("post_id",),
        phrasings=("unlike that", "unlike post two", "take the like off post two",
                   "unlike 1234567890123"),
        success="Unliked {who}'s post {post_id}, Emperor.{note}",
        failure="I did not unlike it, sir. {reason} {alternative}",
        audit="UNLIKE x post {post_id}",
        note="The reverse of x.like: same target rule, same hold, same press boundary.",
    ),
    # ── THE MEDIA ROUND (2026-09-22), part 2: bookmark/unbookmark — x.like's
    #    shape EXACTLY (one status id by shape, resolved once, the hold armed
    #    on the id, `frozen`, every press through `_press`) on the Bookmark
    #    button beside Like. Private to his account and reversible; its own
    #    key (x.bookmark) so it can be withdrawn without losing likes.
    _spec(
        name="x.bookmark", tier="amber", capability="x.bookmark",
        handler=x_tools.bookmark, holds=True,
        frozen=("post_id",),
        phrasings=("bookmark that", "save that post", "bookmark post two", "bookmark 1234567890123"),
        success="Bookmarked {who}'s post {post_id}, Emperor.{note}",
        failure="I did not bookmark it, sir. {reason} {alternative}",
        audit="BOOKMARK x post {post_id}",
        note="By STATUS ID, x.like's shape: an ordinal resolves against the last read's snapshot "
             "once, at request time; the hold is armed on the id; one press on the Bookmark "
             "button. Private, idempotent. Reverse: x.unbookmark.",
    ),
    _spec(
        name="x.unbookmark", tier="amber", capability="x.bookmark",
        handler=x_tools.unbookmark, holds=True,
        frozen=("post_id",),
        phrasings=("unbookmark that", "remove that bookmark", "remove post two from my bookmarks"),
        success="Removed {who}'s post {post_id} from your bookmarks, Emperor.{note}",
        failure="I did not remove it, sir. {reason} {alternative}",
        audit="UNBOOKMARK x post {post_id}",
        note="The reverse of x.bookmark: same target rule, same hold, same press boundary.",
    ),
    _spec(
        name="x.follow", tier="amber", capability="x.follow",
        handler=x_tools.follow, holds=True,
        frozen=("handle",),
        phrasings=("follow @ada", "follow ada on x"),
        success="Following {who}, Emperor.{note}",
        failure="I did not follow them, sir. {reason} {alternative}",
        audit="FOLLOW x @{handle}",
        note="ONE @name, frozen; the hold names it before any navigation. The Follow button "
             "is matched on its exact accessible name, so suggested accounts on the same "
             "page are unreachable. Idempotent. Reverse: x.unfollow.",
    ),
    _spec(
        name="x.unfollow", tier="amber", capability="x.follow",
        handler=x_tools.unfollow, holds=True,
        frozen=("handle",),
        phrasings=("unfollow @ada", "unfollow ada on x"),
        success="Unfollowed {who}, Emperor.{note}",
        failure="I did not unfollow them, sir. {reason} {alternative}",
        audit="UNFOLLOW x @{handle}",
        note="The reverse of x.follow. Presses X's own Unfollow confirmation as a second, "
             "checked, single press.",
    ),
    # ── REPOST, FIXED (X features round 3, 2026-09-12). It addressed a post by
    #    POSITION on a live page — the hazard round 2 rebuilt x.like away from
    #    — and now has x.like's shape exactly: one status id by shape, resolved
    #    once against the last read's snapshot, the hold armed on the id,
    #    `frozen` so a card may not retarget it, every press through `_press`,
    #    and a reverse one spec down.
    _spec(
        name="x.repost", tier="amber", capability="x.interact",
        handler=x_tools.repost, holds=True,
        frozen=("post_id",),
        phrasings=("repost that", "retweet post three", "retweet the second one",
                   "repost 1234567890123"),
        success="Reposted {who}'s post {post_id}, Emperor.{note}",
        failure="I did not repost it, sir. {reason} {alternative}",
        audit="REPOST x post {post_id}",
        note="By STATUS ID, never a position. An ordinal resolves against the last read's "
             "snapshot once, at request time; the hold is armed on the id. X's second-ask "
             "menu item is matched on its exact name, so Quote beside it is unreachable. "
             "Idempotent. Reverse: x.unrepost.",
    ),
    _spec(
        name="x.unrepost", tier="amber", capability="x.interact",
        handler=x_tools.unrepost, holds=True,
        frozen=("post_id",),
        phrasings=("unrepost that", "undo the repost on post two", "unrepost 1234567890123"),
        success="Undid the repost of {who}'s post {post_id}, Emperor.{note}",
        failure="I did not undo it, sir. {reason} {alternative}",
        audit="UNREPOST x post {post_id}",
        note="The reverse of x.repost: same target rule, same hold, same press boundary.",
    ),
    # ── THE MEDIA ROUND (2026-09-22), part 3: save a post's image(s) or its
    #    video to disk. GREEN on its own key (x.media_save): nothing is
    #    published or changed on X — but a FILE is written, so the folder is
    #    fixed (Pictures\Tessa, Videos\Tessa — never a spoken path), every
    #    name is checked against permissions.yaml protected_paths through
    #    files.py's loader, the file is opened create-only, the bytes come
    #    from pbs.twimg.com / video.twimg.com only, the post's words are
    #    fenced as external. A video is ONE progressive MP4 rendition from
    #    the page's own data (best that fits the 50 MB metered-data cap);
    #    an HLS-only video is refused unless ffmpeg is on PATH — it is not
    #    installed here and is never installed by this code.
    _spec(
        name="x.save_image", tier="green", capability="x.media_save",
        handler=x_tools.save_image, holds=True,
        frozen=("post_id",),
        phrasings=("save that image", "download the picture", "save the photo on post two"),
        success="{spoken}",
        failure="I did not save it, sir. {reason} {alternative}",
        audit="SAVE x images from post {post_id}",
        note="Writes NEW files only, under Pictures\\Tessa: full-size originals (name=orig) of "
             "the post's photos, up to four, fetched through the browser session; names checked "
             "against the protected roots; create-only. The post's text and alt text are fenced.",
    ),
    _spec(
        name="x.save_video", tier="green", capability="x.media_save",
        handler=x_tools.save_video, holds=True,
        frozen=("post_id",),
        phrasings=("save that video", "download the video", "save the video on post two"),
        success="{spoken}",
        failure="I did not save it, sir. {reason} {alternative}",
        audit="SAVE x video from post {post_id}",
        note="ONE new file under Videos\\Tessa: the best MP4 rendition X's own page data lists "
             "that fits the 50 MB cap (HEAD first; over the cap is refused with the size). "
             "HLS-only videos need ffmpeg (absent here; never installed by this code).",
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
        # THE TARGET IS NOT WORDING. The card may correct what the reply SAYS;
        # it may not change which post it lands under. Defence in depth for the
        # legacy index path too — `system.x.post_reply` is the id-targeted
        # flow, but if this older tool is ever approved, its aim is fixed at
        # the moment he read the card.
        frozen=("index", "reply_to_id"),
        phrasings=("reply to that", "answer post two", "respond to the first one"),
        success="Replied, Emperor.",
        audit="REPLY to x post {index}: {text}",
        note="RED. Executes ONLY via cmd.permission.respond, never from voice.",
    ),
    # ── THE PUBLISHING ROUND 3 (2026-09-12): quote-post and threads. RED —
    #    both are posting, public and permanent under his name — and both go
    #    out through the proven `post` / `reply` compose path behind the same
    #    card. THE TARGET IS RESOLVED BEFORE THE CARD (`resolve`, the red twin
    #    of ToolHold.resolved): "quote post two" reaches the card as the frozen
    #    status id plus the quoted post's author, text and injection count, so
    #    he never approves blind; a thread reaches it as the frozen list of
    #    posts, in order. EVERYTHING on both cards is frozen — the text too:
    #    a quote is aimed at a post, a thread is approved as a unit, and a
    #    changed word is a changed approval. Deny and say it again.
    _spec(
        name="x.quote", tier="red", capability="x.quote",
        handler=x_tools.quote, holds=True,
        resolve=x_tools._quote_args, describe=x_tools._describe_quote,
        frozen=("text", "quoted_id", "quoted_author", "quoted_text", "quoted_url",
                "injection_seen"),
        phrasings=("quote that tweet with well said", "quote post two saying this is the one",
                   "quote 1234567890123 with this aged well"),
        success="Quoted {quoted_author}'s post {quoted_id}, Emperor. {chars} characters.{warned}",
        failure="I did not quote it, sir. {reason} {alternative}",
        audit="QUOTE x post {quoted_id}: {text}",
        note="RED, card-only. A quote is `post(text + permalink)` — X renders a trailing "
             "status URL as a quote card — so it is the proven compose path and the same "
             "no-approval refusal. quoted_id and the text are FROZEN; the card shows the "
             "quoted post and whether it carried an injection attempt. A post she has not "
             "read is refused before the card: the card could not show it.",
    ),
    _spec(
        name="x.thread", tier="red", capability="x.thread",
        handler=x_tools.thread, holds=True,
        resolve=x_tools._thread_args, describe=x_tools._describe_thread,
        frozen=("posts", "topic"),
        phrasings=("post a thread: first part; second part; third part",
                   "tweet a thread about the audit log"),
        success="Thread posted, Emperor. {n} posts, the first is {first_id}.",
        failure="The thread did not go out, sir. {reason} {alternative}",
        audit="THREAD to x: {posts}",
        note="RED, card-only, ONE card for the whole thread, the list FROZEN. Posted by "
             "id-chain: part 1 through `post`, each later part through `reply` under the id "
             "X returned for the part before (`_posted_id`, never a position). A part that "
             "fails, or whose id cannot be read, STOPS the thread: THREAD-STOPPED on the "
             "chain with the ids that are public, and the failure spoken. 2 to 10 parts. "
             "With a topic and no parts, the parts are drafted in his voice first.",
    ),
    # ── DIRECT MESSAGES (X features round 4, the LAST, 2026-09-12). PRIVATE:
    #    a DM is his correspondence, not strangers' public text. READ is green
    #    on its own key and flagged `private`: the executor never hands its
    #    result to the claim or style stores (Executor._note_claims /
    #    _absorb_style return on the flag, and the result carries `messages`,
    #    never `posts`), and the daemon withholds the turn's heard/said text
    #    from the chain; the chain gets READ-DM with the handle and a COUNT.
    #    SEND is red on its own key: ONE @name by shape, the words resolved
    #    BEFORE the card (`_send_dm_args` — bulk, empty, over-length and a
    #    conversation he has not read in the last 30 minutes are refused
    #    before he is asked), recipient AND message FROZEN, the message
    #    withheld from every chain line (`private_args`: length + sha256 in
    #    its place), and the one press is `_send_dm`, the send boundary.
    _spec(
        name="x.read_dm", tier="green", capability="x.dm_read",
        handler=x_tools.read_dm, private=True,
        phrasings=("read my dms", "what did @ada message me", "read my dms with ada"),
        success="{spoken}",
        failure="I could not read your messages, sir. {reason} {alternative}",
        audit="read x DMs {handle}",
        note="PRIVATE. His inbox, or one conversation by @name. Fenced as external, NEVER "
             "learned (claims/style), chain holds the handle and a count only. One press: "
             "the profile's Message button, which opens the conversation and sends nothing. "
             "⚠ Live DM selectors are unverified until he signs in; a mismatch fails closed.",
    ),
    _spec(
        name="x.send_dm", tier="red", capability="x.dm_send",
        handler=x_tools.send_dm, holds=True, private=True,
        resolve=x_tools._send_dm_args, describe=x_tools._describe_send_dm,
        frozen=("handle", "text", "conversation_url", "read_n"),
        private_args=("text",),
        phrasings=("dm @ada saying see you at six", "message ada saying thanks",
                   "send @ada a dm: on my way"),
        success="Sent to {handle}, Emperor. {chars} characters.",
        failure="I did not send it, sir. {reason} {alternative}",
        audit="DM to x @{handle} ({conversation_url}): {text}",
        note="RED, card-only. ONE recipient by exact @name (bulk refused by shape before the "
             "card); recipient AND message FROZEN; refused before the card unless he read that "
             "conversation in the last 30 minutes (no reply blind); the message text is "
             "withheld from the chain (length + sha256 digest). Sends only into the "
             "conversation URL the read captured, after the page names the handle. ⚠ Live DM "
             "selectors unverified until he signs in; a mismatch fails closed, never a wrong send.",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# CLAIMS — what strangers said on X, as claims. core/brain/claims.py.
# ─────────────────────────────────────────────────────────────────────────────
#
# No `note` tool: a post is noted because it was READ (Executor._note_claims,
# after the fence), never because something asked for it to be remembered.
# All three are green because none can act; `confirm` and `reject` refuse
# every origin but the owner's, by signature.

_CLAIMS = [
    _spec(
        name="claims.recall", tier="green", capability="memory.claims",
        handler=claims_tools.recall,
        phrasings=("what have you heard about the naira", "any claims about bitcoin",
                   "what are people saying about the election"),
        success="{spoken}",
        failure="I could not check, sir. {reason} {alternative}",
        audit="recall claims about {topic}",
        note="Two labelled halves — what HE said (the thread) and what strangers "
             "claimed — never merged. A claim is spoken as unverified unless he "
             "confirmed it himself.",
    ),
    _spec(
        name="claims.confirm", tier="green", capability="memory.claims",
        handler=claims_tools.confirm,
        phrasings=("confirm that claim", "that claim is true",
                   "confirm the claim about the naira"),
        success="Confirmed by you, Emperor: {claim}. I will say so from now on.",
        failure="I did not mark it, sir. {reason} {alternative}",
        audit="confirm claim about {topic}",
        note="THE ONLY PROMOTION PATH. Refuses any provenance but human: the handler "
             "is handed the call's resolved origin by signature, never a key from args.",
    ),
    _spec(
        name="claims.reject", tier="green", capability="memory.claims",
        handler=claims_tools.reject,
        phrasings=("that claim is false", "reject that claim",
                   "the claim about bitcoin is a lie"),
        success="Marked false, Emperor: {claim}. If it comes round again I will say you called it.",
        failure="I did not mark it, sir. {reason} {alternative}",
        audit="reject claim about {topic}",
        note="Owner only, same gate as confirm. A rejected claim is remembered as "
             "rejected, so the same lie coming round again meets his verdict.",
    ),
]


REGISTRY: dict[str, ToolSpec] = {
    s.name: s for s in (_FILES + _WINDOWS + _PROCS + _CLIP + _SYSTEM + _SHELL
                        + _BROWSER + _X + _CLAIMS)
}

# ─────────────────────────────────────────────────────────────────────────────
# core/system — the capability framework. SAME TABLE, SAME VALIDATION.
# ─────────────────────────────────────────────────────────────────────────────
#
# Each ability is one module under core/system/abilities/. Its tier is READ
# from permissions.yaml through core.security.guard at bind time — a module
# declares what it expects, the guard decides, a disagreement refuses to bind
# and an unlisted key refuses to register (CapabilityError, daemon does not
# start). They land here so `_validate()` below holds them to the same rule
# as every hand-written spec, and so the executor, the tests and the report
# see one registry. Imported here rather than at the top of the module
# because core.system needs core.tools.base and this package is mid-import;
# see the note in core/system/capability.py.
from core.system.capability import bound_specs as _system_specs  # noqa: E402

for _spec in _system_specs():
    if _spec.name in REGISTRY:
        raise RuntimeError(f"core/system: {_spec.name} collides with an existing tool")
    REGISTRY[_spec.name] = _spec
del _spec


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
