"""
core/tools/browser.py — a real browser, driven by Playwright.

THE PROFILE DECISION, RESTATED BECAUSE IT IS THE WHOLE SECURITY POSTURE

A DEDICATED `user_data_dir` under `%LOCALAPPDATA%\\Tessa\\browser-profiles\\`.
NEVER his main Chrome profile, for two independent reasons, either of which
would be sufficient:

  1. His main profile holds every session he is signed into — bank, email,
     GitHub, everything. Handing that to an automated agent that reads pages
     containing attacker-controlled text is handing those sessions to the pages.
  2. Playwright cannot attach to a profile Chrome already holds locked. Trying
     it produces either a failure or a second Chrome fighting over the same
     directory.

Revoking everything she can reach in a browser is therefore `rmdir` on one
folder. That is the property worth having.

`channel="chrome"` drives his INSTALLED Chrome. No Chromium download, no
metered bytes. Verified: `C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe`.

HEADFUL, NOT HEADLESS, AND THAT IS DELIBERATE

He has to log into X himself, once, in this profile — which is impossible if the
window never appears. It also means every action she takes in the browser is
visible while it happens, on a machine where the alternative is an invisible
agent clicking things in a session he cannot watch.

EVERYTHING THIS MODULE RETURNS IS UNTRUSTED

Page text, accessible names, ARIA labels, alt text, search results, timelines.
All of it is `Provenance.EXTERNAL`. Handlers return it under `external_text`
with an `external_source`, and the executor fences it — see
`core/brain/executor.py` and `core/brain/provenance.py`. A live page is a worse
target than fetched text because it can hide instructions in places a text
fetch never sees, which is why `read_page` deliberately harvests the hidden
places too rather than only what a human would read.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .base import ToolError, ToolHold

PROFILE_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Tessa" / "browser-profiles"
DEFAULT_PROFILE = PROFILE_ROOT / "default"

#: Where the live Chrome's PID is recorded, so a daemon that was force-killed
#: can reap the orphan it left behind on its next start. See `reap_orphan`.
PID_FILE = PROFILE_ROOT / "chrome.pid"

#: Close the browser after this long with no browser tool use.
#:
#: FIVE MINUTES, and the number comes from what it costs to keep open rather
#: than from taste — measured below in the report. Chromium is the heaviest
#: thing this daemon can hold on a 2-core machine where the Orb is already
#: rendering. Shorter and he pays the ~2 s cold launch repeatedly during one
#: task; longer and an idle browser sits on hundreds of megabytes all evening
#: because he asked one question at lunchtime.
IDLE_TIMEOUT_S = 300.0

#: Page loads on a metered link with two cores are not fast. This is generous
#: enough not to fail on a slow page and short enough that a dead link does not
#: hold the daemon that owns his microphone.
NAV_TIMEOUT_MS = 30_000

#: Cap on extracted page text. CONTRACT §1 caps a frame at 1 MiB, and a model
#: context is smaller than that anyway.
MAX_PAGE_CHARS = 40_000

#: How long a window opened FOR HIM TO USE — the X login page — stays up with
#: no tool call. The idle timer above counts TOOL use, and a hand login with a
#: password and a 2FA code is not tool use: at five minutes it closed the login
#: page underneath him. Fifteen minutes is generous for one sign-in and still
#: bounded. `BrowserSession.hold()` applies it; `x_tools.open_for_login` asks.
LOGIN_GRACE_S = 900.0

#: THE LAUNCH FINGERPRINT, MEASURED ON THIS MACHINE (2026-09-22).
#:
#: He saw "unsupported" on the X login page and the window later closed on its
#: own. The browser was ALREADY his real Chrome (`channel="chrome"`, Program
#: Files, 152.0.7977.84) and `navigator.webdriver` was already `false`. What he
#: was reading was CHROME'S OWN infobar across the top of the page: "You are
#: using an unsupported command-line flag: --no-sandbox. Stability and security
#: will suffer." Playwright passes `--no-sandbox` by default; Chromium's
#: bad-flags list (chrome/browser/ui/startup/bad_flags_prompt.cc) names it, and
#: names `--disable-blink-features` right after it. Hence:
#:
#:   * `chromium_sandbox=True` — drops `--no-sandbox`. Chrome's sandbox works
#:     on Windows; Playwright only turns it off for Linux CI convenience.
#:   * `--disable-blink-features=AutomationControlled` STAYS. It is what makes
#:     `navigator.webdriver` read `false`. Measured: without it the value is
#:     `true` under remote debugging even with `--enable-automation` gone.
#:     It is on the bad-flags list too, which is why the next switch exists.
#:   * `--test-type` — Chromium's own switch for suppressing that infobar.
#:     Invisible to a page; Playwright passes it itself for app windows.
#:   * `--enable-automation` is IGNORED explicitly. Playwright 1.60 no longer
#:     passes it, but it is the flag that sets `webdriver = true` and shows the
#:     "controlled by automated test software" bar, and a Playwright upgrade
#:     must not be able to bring it back.
#:
#: Verified with a window-level capture: the bar is there with the defaults
#: and gone with this set, `navigator.webdriver === false` throughout.
_IGNORED_DEFAULT_ARGS = ["--enable-automation"]
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--test-type"]

#: Belt-and-braces for `navigator.webdriver`, run before any page script. With
#: AutomationControlled disabled Chrome already reports `false` — exactly what
#: an un-automated Chrome reports — so this only acts if some future default
#: drags the value back to `true`. It reports FALSE, not `undefined`: real
#: Chrome never has an undefined `navigator.webdriver`, and `undefined` is the
#: classic stealth-plugin fingerprint.
_WEBDRIVER_MASK_JS = """
(() => {
  try {
    if (navigator.webdriver === true) {
      Object.defineProperty(Navigator.prototype, 'webdriver',
                            {get: () => false, configurable: true, enumerable: true});
    }
  } catch (e) {}
})();
"""


class BrowserUnavailable(ToolError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# ONE THREAD OWNS THE BROWSER
# ─────────────────────────────────────────────────────────────────────────────
#
# THE CRASH THIS FIXES (2026-09-22): "TargetClosedError: BrowserContext.new_page:
# Target page, context or browser has been closed" the moment he asked for X.
#
# Playwright's sync API is a greenlet dispatcher bound to the THREAD that called
# `sync_playwright().start()`. The daemon runs every tool on whatever
# `asyncio.to_thread` worker is free — a typed turn, a voice turn, the approval
# path and the idle reaper are four different threads — so the first browser
# call launched Chrome on one thread and the next one drove it from another.
# Measured here: a cross-thread `new_page()` raises `greenlet.error: Cannot
# switch to a different thread`, leaves its task stranded on Playwright's
# loop, and the stranded task later surfaces as TargetClosedError. The reaper's
# cross-thread `close()` failed the same way, was swallowed, and dropped `_ctx`
# while Chrome kept running on the profile.
#
# So every Playwright call now runs on ONE thread owned by the session. Handlers
# are not edited: `page()` and `context()` hand back a proxy that marshals each
# attribute read and method call onto that thread and wraps whatever comes
# back. Two call sites in twenty handlers cannot forget it, because there is
# nothing to remember.

#: How long a single marshalled call may take before the caller is told the
#: browser is not answering. Longer than any Playwright timeout in this file,
#: so a real navigation timeout arrives as itself and this only fires on a
#: browser that has genuinely hung.
CALL_TIMEOUT_S = 120.0


def _is_pw(obj: Any) -> bool:
    """A Playwright sync object, of any class."""
    return type(obj).__module__.startswith("playwright")


def _unwrap(value: Any) -> Any:
    if isinstance(value, _OnBrowserThread):
        return value._target
    if isinstance(value, (list, tuple)):
        return type(value)(_unwrap(v) for v in value)
    return value


def _wrap(value: Any, session: "BrowserSession") -> Any:
    if _is_pw(value):
        return _OnBrowserThread(value, session)
    if isinstance(value, (list, tuple)):
        return type(value)(_wrap(v, session) for v in value)
    return value


class _OnBrowserThread:
    """
    A Playwright object whose every use runs on the session's browser thread.

    Attribute reads (`page.url`, `ctx.pages`, `locator.first`) are marshalled
    because some of them touch the dispatcher; methods come back as callables
    that marshal the call; results that are Playwright objects are wrapped
    again, so a Locator's `.first.click()` chain stays on the thread end to
    end. Proxies passed as ARGUMENTS (`a.or_(b)`) are unwrapped first.
    """

    __slots__ = ("_target", "_session")

    def __init__(self, target: Any, session: "BrowserSession") -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_session", session)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        target, session = self._target, self._session
        attr = session.call(lambda: getattr(target, name))
        if callable(attr) and not _is_pw(attr):
            def method(*args: Any, **kwargs: Any) -> Any:
                a = _unwrap(list(args))
                k = {key: _unwrap(v) for key, v in kwargs.items()}
                return _wrap(session.call(lambda: attr(*a, **k)), session)
            method.__name__ = name
            return method
        return _wrap(attr, session)

    def __setattr__(self, name: str, value: Any) -> None:
        target, session = self._target, self._session
        session.call(lambda: setattr(target, name, value))

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"<on browser thread: {self._target!r}>"


def _reap_profile_chrome(profile: Path) -> str:
    """
    Terminate a chrome.exe running against ONE OF HER profile directories
    that this process does not control.

    Called only when `_ctx` is None and a launch is about to happen against
    `profile`: a second Chrome on the same user_data_dir hands off to the
    first and exits, and Playwright then reports a browser it never got. The
    ownership test is the same as `reap_orphan`'s — the exact profile path on
    the command line, a path no Chrome of his ever carries — and it is
    refused outright for anything outside `PROFILE_ROOT`, so it can never
    touch a Chrome running on his own user data.
    """
    if not _is_tessa_profile(profile):
        return ""
    try:
        import psutil

        want = f"--user-data-dir={profile}".lower()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            if (p.info["name"] or "").lower() != "chrome.exe":
                continue
            argv = [str(a).lower() for a in (p.info["cmdline"] or [])]
            if want not in argv or any(a.startswith("--type=") for a in argv):
                continue
            pid = p.info["pid"]
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                p.kill()
            return f"closed a chrome.exe({pid}) already running on {profile.name}"
    except Exception:  # noqa: BLE001
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

def _is_tessa_profile(path: Path) -> bool:
    """
    True only for a directory under HER profile root.

    Used before any `mkdir`: she may create her own profiles and must never
    create, or half-create, a directory inside his Chrome user data.
    """
    try:
        return PROFILE_ROOT.resolve() in Path(path).resolve().parents
    except (OSError, ValueError):
        return False


def _chrome_executable() -> str:
    """
    Where his installed Chrome is, for the fallback launch.

    `channel="chrome"` is Playwright's own lookup and is tried first. If it
    fails — an enterprise policy, a Playwright that lost the channel — this
    finds chrome.exe the way Windows does: the App Paths registry key, then the
    standard install folders. Empty when there is no Chrome at all.
    """
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    exe = str(winreg.QueryValue(k, None) or "").strip().strip('"')
                if exe and Path(exe).is_file():
                    return exe
            except OSError:
                continue
    except ImportError:
        pass
    for base in (os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        if not base:
            continue
        exe = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if exe.is_file():
            return str(exe)
    return ""


class BrowserSession:
    """
    One lazily-launched, persistently-profiled Chrome.

    LAZY, and the reason is arithmetic: launching at daemon start would add the
    cold-launch wall clock to every boot and hold Chromium's RSS for the entire
    session on a machine with 15.9 GB where the Orb renders at 30 fps — for a
    tool he might not use that day at all.

    THREE THINGS CLOSE IT, and I want the reasoning on record because the brief
    asked for a choice:

      * IDLE TIMEOUT — the common case. He asks one thing, wanders off, and the
        browser should not still be resident an hour later.
      * EXPLICIT INTENT ("close the browser") — because the idle timer is
        invisible to him, and a resource he can see in Task Manager but cannot
        dismiss by asking is one he will start killing by hand.
      * DAEMON SHUTDOWN — non-negotiable, item 2c. A headful Chrome that
        outlives the daemon is a process he did not start and cannot attribute,
        sitting in a profile with his X session in it.

    All three, not one. They cover different failure modes: the timer handles
    forgetting, the intent handles impatience, and shutdown handles the case
    that actually matters for trust.
    """

    def __init__(self) -> None:
        self._pw: Any = None
        self._ctx: Any = None
        self._lock = threading.RLock()
        #: THE BROWSER THREAD — see `_OnBrowserThread`. Started on first use,
        #: daemon so it never holds the interpreter open, and the only thread
        #: that ever touches `_pw` or `_ctx` or anything they hand back.
        self._thread: threading.Thread | None = None
        self._queue: "queue.Queue[tuple[Callable[[], Any], concurrent.futures.Future]]" = queue.Queue()
        #: Set from Playwright's own `close` event when the context goes away
        #: under us — Chrome crashed, or a profile Chrome closed itself. The
        #: window-closed case fires NO event (measured), so `_alive()` checks
        #: the page count as well.
        self._ctx_gone = False
        self.last_used = 0.0
        self.launched_at = 0.0
        self.cold_launch_s = 0.0
        self.chrome_pid: int | None = None
        self._reaper: threading.Thread | None = None
        #: WHICH profile is currently open. Added when profiles became named
        #: rather than hardcoded: with one profile "is it open" was the whole
        #: question, and with several the question is "is the RIGHT one open".
        #: Reported by `status()` so he can always ask whose session she is in.
        self.profile: Path = DEFAULT_PROFILE
        self.profile_directory: str = ""
        #: "channel=chrome" or "executable_path=<chrome.exe>" — which way the
        #: live Chrome was found. Reported by `status()` and by X's login tool.
        self.launch_via: str = ""
        #: The last Chrome `_reap_profile_chrome` had to close before a launch,
        #: or "". Reported by `status()` so a hand-off death leaves a trace.
        self.last_reaped: str = ""

    # ── the browser thread ───────────────────────────────────────────────────

    def _pump(self) -> None:
        while True:
            fn, fut = self._queue.get()
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn())
                except BaseException as exc:  # noqa: BLE001 — re-raised in the caller
                    fut.set_exception(exc)

    def call(self, fn: Callable[[], Any], timeout: float = CALL_TIMEOUT_S) -> Any:
        """
        Run `fn` on the browser thread and return its result (or re-raise).

        Re-entrant: code already on that thread — a route handler Playwright
        invoked, a proxy used from inside another proxied call — runs inline,
        because queueing behind itself would deadlock.
        """
        if self._thread is not None and threading.current_thread() is self._thread:
            return fn()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._pump, name="tessa-browser", daemon=True)
            self._thread.start()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((fn, fut))
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise BrowserUnavailable(
                f"the browser did not answer within {timeout:.0f} seconds",
                "Say close the browser and ask me again.") from None

    def _alive(self) -> bool:
        """
        Is the context we hold still one Chrome will honour? BROWSER THREAD ONLY.

        Three checks, because they fail differently. The `close` event covers a
        crash or a profile Chrome shutting itself down. `is_connected()` covers
        a dead pipe. And ZERO PAGES covers the case that actually bit him: he
        closed the window himself, Chrome stayed resident with no window, no
        event fired, `is_connected()` stayed True — and `new_page()` on that
        context raises exactly "Target page, context or browser has been
        closed". Measured on real Chrome 152, headful, 2026-09-22.
        """
        if self._ctx is None or self._ctx_gone:
            return False
        try:
            browser = getattr(self._ctx, "browser", None)
            if browser is not None and not browser.is_connected():
                return False
            return len(self._ctx.pages) > 0
        except Exception:  # noqa: BLE001
            return False

    # ── launch ───────────────────────────────────────────────────────────────

    def _require_playwright(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserUnavailable(
                "Playwright is not installed",
                "The browser tools need it. Nothing else is affected.") from None
        return sync_playwright

    def context(self, profile: Path | None = None, profile_directory: str = "") -> Any:
        """
        The live context, launching Chrome on first use.

        `profile` DEFAULTS TO HER OWN, so every caller written before profiles
        were named keeps its exact behaviour. `profile_directory` is Chrome's
        own subdirectory name (`Profile 18`) and is only used when opening one
        of HIS profiles, where the user_data_dir is the shared root and the
        profile is selected with a switch.

        SWITCHING PROFILES CLOSES THE OLD ONE FIRST. One Chrome, one profile:
        Playwright holds a persistent context against a user_data_dir, and
        leaving his personal session open behind her own would mean two live
        browsers and no clear answer to "whose session are you in". `_lock` is
        an RLock, so closing from in here is safe.
        """
        target = Path(profile) if profile is not None else DEFAULT_PROFILE
        with self._lock:
            if self._ctx is not None:
                same = (self.profile == target
                        and self.profile_directory == (profile_directory or ""))
                if same and self.call(self._alive):
                    # max(): a `hold()` in force is never shortened by use.
                    self.last_used = max(self.last_used, time.monotonic())
                    return _OnBrowserThread(self._ctx, self)
                # Either the wrong profile is open, or the one we hold is dead
                # — he closed the window, Chrome went away. A dead context is
                # closed and RELAUNCHED rather than handed back to fail, which
                # is the whole of the TargetClosedError fix at this call site.
                self.close(reason=f"switching to {target.name}" if not same
                           else "the browser window had been closed")

            sync_playwright = self._require_playwright()
            if _is_tessa_profile(target):
                # Only ever create HER directories. His already exist, and
                # mkdir-ing into his Chrome root is not something this should
                # be able to do by accident.
                target.mkdir(parents=True, exist_ok=True)
                # A Chrome we no longer control but which still holds HER
                # profile would make this launch hand off and die under us.
                reaped = _reap_profile_chrome(target)
                if reaped:
                    self.last_reaped = reaped
            elif not target.is_dir():
                raise BrowserUnavailable(
                    f"that profile directory is not there ({target})",
                    "Say list my chrome profiles and I will read you the real names.")
            args = list(_LAUNCH_ARGS)
            if profile_directory:
                args.append(f"--profile-directory={profile_directory}")
            launch = dict(
                user_data_dir=str(target),
                headless=False,            # he must be able to log in himself
                viewport={"width": 1280, "height": 720},
                args=args,
                ignore_default_args=_IGNORED_DEFAULT_ARGS,
                chromium_sandbox=True,     # no --no-sandbox: no bad-flags bar
            )

            def _launch() -> float:
                """ON THE BROWSER THREAD: this is where Playwright's dispatcher is born."""
                t0 = time.perf_counter()
                # A PREVIOUS DISPATCHER ON THIS THREAD MUST BE GONE FIRST. If a
                # handle was lost without `stop()`, its loop is still marked
                # running on this thread and `sync_playwright()` refuses with
                # "using Playwright Sync API inside the asyncio loop". Stop what
                # is left, then clear the stale marker — there is no genuine
                # loop running here; the browser thread is a plain worker.
                if self._pw is not None:
                    self._shutdown_playwright()
                try:
                    import asyncio

                    asyncio._set_running_loop(None)
                except Exception:  # noqa: BLE001
                    pass
                self._pw = sync_playwright().start()
                try:
                    # HIS Chrome, by channel. No Chromium download, no metered bytes.
                    self._ctx = self._pw.chromium.launch_persistent_context(
                        channel="chrome", **launch)
                    self.launch_via = "channel=chrome"
                except Exception as exc:  # noqa: BLE001
                    # The same Chrome by path — only if Windows knows where it is.
                    exe = _chrome_executable()
                    if not exe:
                        self._shutdown_playwright()
                        raise BrowserUnavailable(
                            f"Chrome would not start: {type(exc).__name__}",
                            "Close any Chrome running from this profile and ask me again.") from None
                    try:
                        self._ctx = self._pw.chromium.launch_persistent_context(
                            executable_path=exe, **launch)
                        self.launch_via = f"executable_path={exe}"
                    except Exception as exc2:  # noqa: BLE001
                        self._shutdown_playwright()
                        raise BrowserUnavailable(
                            f"Chrome would not start: {type(exc2).__name__}",
                            "Close any Chrome running from this profile and ask me again.") from None
                self._ctx_gone = False
                self._ctx.on("close", lambda _c: setattr(self, "_ctx_gone", True))
                self._ctx.set_default_timeout(NAV_TIMEOUT_MS)
                self._ctx.add_init_script(_WEBDRIVER_MASK_JS)
                return time.perf_counter() - t0

            self.cold_launch_s = self.call(_launch)
            self.profile = target
            self.profile_directory = profile_directory or ""
            self.launched_at = time.monotonic()
            self.last_used = self.launched_at
            self._record_pid()
            self._start_reaper()
            return _OnBrowserThread(self._ctx, self)

    def _record_pid(self) -> None:
        """
        Write the browser process id to disk.

        THE FORCE-KILL HOLE, NAMED RATHER THAN PAPERED OVER: `atexit` does not
        run when the daemon is killed with `taskkill /F`, which is exactly how I
        kill daemons. In that case Chrome survives, and item 2c says it must
        never outlive the daemon. This file is how the NEXT daemon start finds
        and closes the orphan — see `reap_orphan`, called from server.py.
        """
        try:
            import psutil

            browser = getattr(self._ctx, "browser", None)
            proc = getattr(self._pw.chromium, "_connection", None)
            _ = browser, proc
            # Playwright does not expose the Chrome pid directly; find the
            # chrome.exe whose command line names OUR profile directory. This is
            # a targeted match on a path we own, not a match on an image name.
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                if (p.info["name"] or "").lower() != "chrome.exe":
                    continue
                cl = " ".join(p.info["cmdline"] or [])
                if str(DEFAULT_PROFILE).lower() in cl.lower():
                    self.chrome_pid = p.info["pid"]
                    break
            if self.chrome_pid:
                PID_FILE.write_text(json.dumps({"pid": self.chrome_pid,
                                                "profile": str(DEFAULT_PROFILE)}),
                                    encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _start_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return

        def run() -> None:
            while True:
                time.sleep(15.0)
                with self._lock:
                    if self._ctx is None:
                        return
                    if time.monotonic() - self.last_used > IDLE_TIMEOUT_S:
                        self.close(reason="idle")
                        return

        self._reaper = threading.Thread(target=run, name="tessa-browser-idle", daemon=True)
        self._reaper.start()

    def hold(self, seconds: float = LOGIN_GRACE_S) -> None:
        """
        Keep the idle reaper off for `seconds` from now.

        For a window that is HIS to use — the login page — where "no tool use"
        is the expected state, not idleness. A later tool call never shortens
        it (`context()` takes the max); an explicit close still closes.
        """
        with self._lock:
            self.last_used = max(self.last_used,
                                 time.monotonic() + float(seconds) - IDLE_TIMEOUT_S)

    # ── teardown ─────────────────────────────────────────────────────────────

    def _shutdown_playwright(self) -> None:
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = None

    def close(self, reason: str = "asked") -> dict[str, Any]:
        with self._lock:
            was_open = self._ctx is not None
            up = time.monotonic() - self.launched_at if self.launched_at else 0.0

            def _close() -> None:
                """ON THE BROWSER THREAD. The reaper, atexit and a tool turn all
                arrive here from their own threads; before this, the reaper's
                close raised a greenlet error that was swallowed, and Chrome
                lived on with `_ctx` already None."""
                try:
                    if self._ctx is not None:
                        self._ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ctx = None
                self._shutdown_playwright()

            try:
                if self._thread is not None and self._thread.is_alive():
                    self.call(_close, timeout=30.0)
                else:
                    _close()
            except Exception:  # noqa: BLE001
                self._ctx = None
                self._pw = None
            if was_open and self.chrome_pid:
                # Playwright's close is asked first; this only acts on a Chrome
                # that ignored it. Same pid, same ownership test as at launch.
                _reap_profile_chrome(self.profile)
            try:
                PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            self.chrome_pid = None
            self._ctx_gone = False
            return {"was_open": was_open, "reason": reason, "up_s": round(up, 1)}

    @property
    def is_open(self) -> bool:
        return self._ctx is not None

    def page(self) -> Any:
        """
        The page every handler drives — a proxy that runs on the browser thread.

        `context()` has already relaunched a dead browser, so `pages` is only
        empty here on a context that is alive and pageless (a headless Chrome
        whose page was closed), where `new_page` genuinely works. If Chrome
        vanishes in the gap all the same, ONE relaunch is tried before the
        error is his to hear — the retry is bounded so a Chrome that will not
        start cannot loop.
        """
        for attempt in (1, 2):
            ctx = self.context()
            try:
                pages = ctx.pages
                return pages[0] if pages else ctx.new_page()
            except Exception as exc:  # noqa: BLE001
                if attempt == 2 or "closed" not in str(exc).lower():
                    raise
                with self._lock:
                    self.close(reason="the browser closed between calls")
        raise BrowserUnavailable("the browser closed twice in a row",
                                 "Ask me again in a moment.")


SESSION = BrowserSession()
atexit.register(lambda: SESSION.close(reason="daemon shutdown"))


def reap_orphan() -> str:
    """
    Close a Chrome left behind by a daemon that was force-killed.

    Called at daemon start. Targets ONE recorded pid, verified to still be a
    chrome.exe running against OUR profile directory before anything is killed —
    the same discipline as `procs.kill`: a pid selected by image name is
    kill-by-name with extra steps.
    """
    try:
        rec = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    pid = int(rec.get("pid", 0))
    if not pid:
        return ""
    try:
        import psutil

        p = psutil.Process(pid)
        if p.name().lower() != "chrome.exe":
            return ""
        # THE OWNERSHIP TEST, and it is a path match rather than a safeproc
        # ancestry walk — deliberately, because ancestry CANNOT work here and
        # saying so is better than a check that looks rigorous and is not.
        #
        # `safeproc.owns()` asks "does this process descend from one I started".
        # An orphan by definition outlived the daemon that launched it, so its
        # parent chain is broken and the answer is always no. What IS provable
        # is that this chrome.exe was launched against TESSA'S OWN profile
        # directory — a path no Chrome of his will ever carry, because he has
        # never opened that folder. That is a stronger claim than image name and
        # it is the one that actually holds for an orphan.
        if str(DEFAULT_PROFILE).lower() not in " ".join(p.cmdline()).lower():
            return ""
        p.terminate()
        p.wait(timeout=5)
        PID_FILE.unlink(missing_ok=True)
        return f"reaped orphaned browser chrome.exe({pid}) from a previous run"
    except Exception:  # noqa: BLE001
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return ""


def status() -> dict[str, Any]:
    """RSS and CPU of the live browser, for the report and for PULSE."""
    out: dict[str, Any] = {"open": SESSION.is_open, "rss_mb": 0.0, "cpu_pct": 0.0,
                           "procs": 0, "cold_launch_s": round(SESSION.cold_launch_s, 2),
                           "launch_via": SESSION.launch_via}
    if not SESSION.is_open:
        return out
    try:
        import psutil

        rss = 0
        n = 0
        cpu = 0.0
        for p in psutil.process_iter(["name", "cmdline"]):
            if (p.info["name"] or "").lower() != "chrome.exe":
                continue
            if str(DEFAULT_PROFILE).lower() not in " ".join(p.info["cmdline"] or []).lower():
                continue
            try:
                rss += p.memory_info().rss
                cpu += p.cpu_percent(None)
                n += 1
            except Exception:  # noqa: BLE001
                continue
        out.update({"rss_mb": round(rss / 1e6, 1), "procs": n, "cpu_pct": round(cpu, 1)})
    except Exception:  # noqa: BLE001
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION — everything a page can hide an instruction in
# ─────────────────────────────────────────────────────────────────────────────

#: THE SCRIPT THAT MAKES THE FENCE HONEST.
#:
#: A text fetch sees rendered text. A live page can carry instructions in places
#: rendered text never reaches, and `browser.click` reads one of those places by
#: design — the accessibility tree. So extraction deliberately harvests:
#:
#:   visible text · display:none and visibility:hidden text · aria-label ·
#:   aria-description · aria-labelledby targets · alt · title · placeholder ·
#:   the accessible NAME of every interactive element
#:
#: All of it is fenced and scanned. Harvesting the hidden places is not
#: thoroughness for its own sake: an injection she cannot see is the only kind
#: worth planting, and one that lands in an accessible name is an injection
#: aimed squarely at `browser.click`.
_EXTRACT_JS = r"""
() => {
  const out = {visible: [], hidden: [], attrs: [], names: []};
  const push = (arr, s) => { if (s && s.trim()) arr.push(s.trim()); };

  const walk = (root) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let el = it.currentNode;
    while (el) {
      const st = window.getComputedStyle(el);
      const hidden = st.display === 'none' || st.visibility === 'hidden'
                     || st.opacity === '0' || el.hasAttribute('hidden')
                     || el.getAttribute('aria-hidden') === 'true';
      for (const a of ['alt','title','placeholder','aria-label','aria-description',
                       'aria-roledescription','data-tooltip']) {
        const v = el.getAttribute && el.getAttribute(a);
        if (v) push(out.attrs, a + '=' + v);
      }
      const lb = el.getAttribute && el.getAttribute('aria-labelledby');
      if (lb) {
        for (const id of lb.split(/\s+/)) {
          const t = document.getElementById(id);
          if (t) push(out.attrs, 'aria-labelledby=' + t.textContent);
        }
      }
      const tag = el.tagName.toLowerCase();
      const role = el.getAttribute && el.getAttribute('role');
      const interactive = ['a','button','input','select','textarea','summary'].includes(tag)
        || ['button','link','menuitem','tab','checkbox','radio','option'].includes(role || '');
      if (interactive) {
        // NEVER `.value`, FOR ANY FIELD. This is P7.
        //
        // `.value` used to be the third fallback for a control's accessible
        // name, which meant an <input type="password"> with no aria-label
        // contributed the TYPED PASSWORD to `external_text` — the string that
        // is placed in the model's context. `browser.read_page` run while a
        // login form was filled, including the owner typing his own X password,
        // handed the model the password.
        //
        // Dropped for EVERY input/textarea/select rather than for password
        // fields only. A type-only rule has to enumerate every way a field can
        // be secret — type=password, autocomplete=current-password /
        // new-password / cc-number / cc-csc / one-time-code, a role=textbox
        // widget, a masked custom field — and the first one it misses is a
        // leak. What is lost is the CONTENTS of filled fields, which is text he
        // typed himself and never needed reading back to him. What a control IS
        // still comes through: aria-label, its innerText (select options,
        // textarea defaults), title, and — added so an unlabelled box still has
        // a name `browser.click` can target — its placeholder.
        const nm = (el.getAttribute('aria-label') || el.innerText
                    || el.getAttribute('title') || el.getAttribute('placeholder')
                    || '').trim();
        if (nm) push(out.names, nm.slice(0, 200));
      }
      const own = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3).map(n => n.nodeValue).join(' ');
      if (own && own.trim()) push(hidden ? out.hidden : out.visible, own);
      el = it.nextNode();
    }
  };
  walk(document.body || document.documentElement);
  return out;
}
"""


def _harvest(page: Any) -> dict[str, Any]:
    try:
        raw = page.evaluate(_EXTRACT_JS)
    except Exception:  # noqa: BLE001
        raw = {"visible": [], "hidden": [], "attrs": [], "names": []}
    visible = " ".join(raw.get("visible", []))[:MAX_PAGE_CHARS]
    hidden = " ".join(raw.get("hidden", []))[:MAX_PAGE_CHARS // 4]
    attrs = " | ".join(raw.get("attrs", []))[:MAX_PAGE_CHARS // 4]
    names = raw.get("names", [])
    # ONE STRING, fenced as a unit. The hidden and attribute sections are
    # LABELLED rather than merged silently, so when she reports what a page
    # tried, she can say WHERE it tried it.
    combined = (
        f"{visible}\n\n"
        f"[HIDDEN ELEMENTS ON THIS PAGE]\n{hidden}\n\n"
        f"[ATTRIBUTES: alt/title/aria]\n{attrs}\n\n"
        f"[ACCESSIBLE NAMES OF CLICKABLE ELEMENTS]\n{' | '.join(names[:200])}"
    )
    return {"visible": visible, "hidden": hidden, "attrs": attrs,
            "names": names, "combined": combined}


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS
# ─────────────────────────────────────────────────────────────────────────────

def _goto(page: Any, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"{url} did not load ({type(exc).__name__})",
                        "Check the address, or your connection.") from None


def open_url(url: str) -> dict[str, Any]:
    u = str(url or "").strip()
    if not u:
        raise ToolError("no address came through", "Say the site and I will open it.")
    # `file://` and `about:` are schemes too. The first version prepended
    # https:// to everything that was not http(s), which turned a local test
    # fixture into "https://file:///C:/..." and failed to load. Caught by the
    # injection fixture, which is exactly the kind of thing it should catch.
    if not u.startswith(("http://", "https://", "file://", "about:")):
        u = "https://" + u
    page = SESSION.page()
    _goto(page, u)
    title = (page.title() or "")[:120]
    return {
        "url": page.url, "title": title,
        "cold_launch_s": round(SESSION.cold_launch_s, 2),
        # THE TITLE IS ATTACKER-CONTROLLED AND SHE SPEAKS IT. Opening a page
        # used to leave the fence at zero while reading that page's <title>
        # aloud — so a hostile site could put a sentence in her mouth AND the
        # next amber action would still fire. Navigating anywhere is now itself
        # an external-content event, which is the honest model: she has been to
        # the page, whether or not she has read the body.
        "external_source": page.url,
        "external_text": f"[PAGE TITLE] {title}",
    }


def close_browser(reason: str = "asked") -> dict[str, Any]:
    """
    `reason` exists because server.py passes one on shutdown.

    IT DID NOT, AND THAT WAS A REAL BUG: the shutdown path called
    `close_browser(reason="daemon shutdown")` against a zero-argument function,
    raising TypeError inside the daemon's shutdown tail — which would have
    skipped the `daemon.stop` audit entry AND `rt.remove_runtime_file()`,
    leaving a stale runtime.json every clean exit. Found by review, confirmed by
    calling it.
    """
    return SESSION.close(reason=reason)


#: DUCKDUCKGO'S HTML ENDPOINT, and the argument for it over Google:
#:
#: Google detects automation aggressively and answers with a consent wall or a
#: CAPTCHA. Neither is a search result, and a search tool whose common outcome
#: is "solve this puzzle" is a tool he stops trusting. DuckDuckGo's `html`
#: endpoint is a server-rendered results page with stable, semantic markup — no
#: JavaScript required to read it, no consent interstitial, and materially more
#: automation-tolerant.
#:
#: The honest cost: DuckDuckGo's result quality on obscure technical queries is
#: below Google's, and it does still rate-limit and can still present an
#: anomaly page. It is the better DEFAULT, not a way around the problem — which
#: is why the block path below is built rather than assumed away.
SEARCH_URL = "https://html.duckduckgo.com/html/?q="

#: Signals that the engine is refusing rather than answering. Detected so she
#: can SAY SO — never so she can work around it.
_BLOCK_MARKERS = (
    "unusual traffic", "are you a robot", "captcha", "recaptcha",
    "verify you are human", "detected unusual", "automated queries",
    "anomaly", "blocked", "rate limit", "too many requests",
)


def search(query: str, limit: int = 5) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        raise ToolError("no search terms came through", "Tell me what to look for.")
    from urllib.parse import quote_plus

    page = SESSION.page()
    _goto(page, SEARCH_URL + quote_plus(q))

    body = (page.inner_text("body") or "") if page else ""
    low = body.lower()
    hit = next((m for m in _BLOCK_MARKERS if m in low), None)
    results: list[dict[str, str]] = []
    try:
        for el in page.query_selector_all("a.result__a")[:limit]:
            t = (el.inner_text() or "").strip()
            href = el.get_attribute("href") or ""
            if t:
                results.append({"title": t[:160], "url": href})
    except Exception:  # noqa: BLE001
        pass

    if not results and hit:
        # SHE STOPS. No guessing, no partial page presented as results, and no
        # attempt at the challenge. A search tool that returns something
        # plausible when it was actually blocked is worse than one that fails.
        raise ToolError(
            f"the search engine blocked me — the page mentions {hit!r}",
            "It thinks I am a robot, which I am. Search it yourself and I will "
            "read the page you land on.")
    if not results:
        raise ToolError("the results page had nothing I could read",
                        "Try different words, or open the site directly.")

    return {"n": len(results), "query": q, "results": results,
            "first": results[0]["title"],
            "head": "; ".join(r["title"][:60] for r in results[:3]),
            "external_source": f"duckduckgo search for {q!r}",
            "external_text": "\n".join(f"{r['title']} — {r['url']}" for r in results)}


def read_page(url: str | None = None) -> dict[str, Any]:
    page = SESSION.page()
    if url:
        u = str(url).strip()
        if not u.startswith(("http://", "https://", "file://", "about:")):
            u = "https://" + u
        _goto(page, u)
    got = _harvest(page)
    return {
        "url": page.url, "title": (page.title() or "")[:120],
        "chars": len(got["combined"]), "visible_chars": len(got["visible"]),
        "hidden_chars": len(got["hidden"]), "attr_chars": len(got["attrs"]),
        "names": len(got["names"]),
        "external_source": page.url,
        "external_text": got["combined"],
    }


def screenshot(path: str | None = None) -> dict[str, Any]:
    page = SESSION.page()
    target = Path(path) if path else (
        Path(os.environ.get("LOCALAPPDATA", ".")) / "Tessa" / "screenshots"
        / f"shot-{int(time.time())}.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=False)
    return {"path": str(target), "name": target.name, "url": page.url}


def click(name: str | None = None, selector: str | None = None) -> dict[str, Any]:
    """
    AMBER. By accessible name, or by an explicit selector.

    IF IT CANNOT FIND IT, IT FAILS. It does not click the nearest thing, and it
    does not click the first of several. On a page — and especially on a
    timeline — the element next to the one he meant is a public action.
    """
    page = SESSION.page()
    if selector:
        el = page.query_selector(selector)
        if el is None:
            raise ToolError(f"nothing on this page matches {selector!r}",
                            "Tell me what the button says instead.")
        el.click()
        return {"what": selector, "how": "selector", "url": page.url}

    n = str(name or "").strip()
    if not n:
        raise ToolError("no button name came through", "Tell me what it says.")
    loc = page.get_by_role("button", name=n).or_(page.get_by_role("link", name=n))
    count = loc.count()
    if count == 0:
        loc = page.get_by_text(n, exact=False)
        count = loc.count()
    if count == 0:
        raise ToolError(f"I cannot find anything called {n!r} on this page",
                        "Read me what it actually says and I will try that.")
    if count > 1:
        raise ToolError(f"{count} things on this page are called {n!r}",
                        "Which one? I will not guess on a page.")
    loc.first.click()
    return {"what": n, "how": "accessible name", "url": page.url}


def type_text(field: str, text: str) -> dict[str, Any]:
    """AMBER. Into a NAMED field. Same no-guessing rule as click."""
    page = SESSION.page()
    f = str(field or "").strip()
    loc = page.get_by_label(f) if f else None
    if loc is None or loc.count() == 0:
        loc = page.get_by_placeholder(f)
    if loc.count() == 0:
        loc = page.get_by_role("textbox", name=f)
    if loc.count() == 0:
        raise ToolError(f"there is no field called {f!r} on this page",
                        "Tell me the label as it appears.")
    if loc.count() > 1:
        raise ToolError(f"{loc.count()} fields are called {f!r}", "Which one?")
    loc.first.fill(str(text or ""))
    return {"field": f, "chars": len(str(text or "")), "url": page.url}


def submit(confirmed: bool = False) -> dict[str, Any]:
    """
    RED (spec §7.2), and it HOLDS — and then it still does not run.

    See `core/tools/__init__.py`: red tools execute ONLY through
    `cmd.permission.respond`, never from voice. The hold below is what he hears
    if this handler is ever reached directly; the gate above it is what actually
    stops it.
    """
    page = SESSION.page()
    if not confirmed:
        raise ToolHold(f"submitting this form on {page.url}")
    page.keyboard.press("Enter")
    return {"url": page.url}
