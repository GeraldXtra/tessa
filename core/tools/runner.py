from __future__ import annotations

import ctypes
import locale
import subprocess
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.security import gate

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_BASIC_PROCESS_ID_LIST = 3
JOB_EXTENDED_LIMIT_INFORMATION = 9
MAX_KEEP_BYTES = 4 * 1024 * 1024
POLL_S = 0.05

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_nt = ctypes.WinDLL("ntdll")


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_k32.SetInformationJobObject.restype = wintypes.BOOL
_k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_k32.AssignProcessToJobObject.restype = wintypes.BOOL
_k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_k32.TerminateJobObject.restype = wintypes.BOOL
_k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.QueryInformationJobObject.restype = wintypes.BOOL
_k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_nt.NtResumeProcess.restype = ctypes.c_long
_nt.NtResumeProcess.argtypes = [wintypes.HANDLE]


class Job:
    def __init__(self) -> None:
        self.handle = _k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                                 | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
        if not _k32.SetInformationJobObject(self.handle, JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                            ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            _k32.CloseHandle(self.handle)
            raise OSError(err, "SetInformationJobObject failed")

    def assign(self, process_handle: int) -> None:
        if not _k32.AssignProcessToJobObject(self.handle, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def pids(self) -> list[int] | None:
        n = 512

        class LIST(ctypes.Structure):
            _fields_ = [("Assigned", wintypes.DWORD), ("InList", wintypes.DWORD), ("Ids", ctypes.c_size_t * n)]

        buf = LIST()
        if not _k32.QueryInformationJobObject(self.handle, JOB_BASIC_PROCESS_ID_LIST, ctypes.byref(buf),
                                              ctypes.sizeof(buf), None):
            return None
        return [int(buf.Ids[i]) for i in range(min(buf.InList, n))]

    def settle(self, seconds: float) -> list[int] | None:
        end = time.perf_counter() + seconds
        while True:
            now = self.pids()
            if not now or time.perf_counter() >= end:
                return now
            time.sleep(0.02)

    def terminate(self, code: int = 1) -> bool:
        return bool(_k32.TerminateJobObject(self.handle, code))

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


@dataclass
class Running:
    label: str
    started: float
    stop: threading.Event = field(default_factory=threading.Event)
    reason: str = ""


_RUNNING: dict[int, Running] = {}
_LOCK = threading.Lock()


def running() -> list[str]:
    with _LOCK:
        return [r.label for r in _RUNNING.values()]


def stop_all(reason: str) -> int:
    with _LOCK:
        live = list(_RUNNING.values())
    for r in live:
        r.reason = reason
        r.stop.set()
    return len(live)


@dataclass
class ProcResult:
    argv: list[str]
    code: int | None
    ms: float
    timed_out: bool
    stopped: bool
    stop_reason: str
    output: bytes
    total_bytes: int
    pids_seen: list[int]
    left_after: list[int]
    started_at: float


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode(locale.getpreferredencoding(False))
    except (UnicodeDecodeError, LookupError):
        return data.decode("utf-8", errors="replace")


def _created(pid: int) -> float | None:
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:
        return None


def _note(seen: dict[int, float | None], pids: list[int] | None) -> None:
    for pid in pids or []:
        if pid not in seen:
            seen[pid] = _created(pid)


def _still_running(seen: dict[int, float | None], seconds: float) -> list[int]:
    import psutil

    end = time.perf_counter() + seconds
    out = []
    for pid, created in sorted(seen.items()):
        try:
            p = psutil.Process(pid)
            if created is not None and abs(p.create_time() - created) > 0.01:
                continue
            p.wait(timeout=max(0.0, end - time.perf_counter()))
        except psutil.TimeoutExpired:
            out.append(pid)
        except psutil.Error:
            continue
    return out


def run_process(argv: list[str], *, cwd: str, env: dict[str, str], timeout_s: float, out_file: Path,
                label: str) -> ProcResult:
    job = Job()
    t0 = time.perf_counter()
    started_at = time.time()
    try:
        proc = gate.start(gate.Start(kind=gate.CODE, why=label, argv=list(argv), mode="popen", options=dict(
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)))
    except BaseException:
        job.close()
        raise
    handle = int(proc._handle)
    try:
        job.assign(handle)
    except OSError:
        proc.kill()
        proc.wait(5)
        job.close()
        raise
    seen: dict[int, float | None] = {proc.pid: _created(proc.pid)}
    keep = bytearray()
    total = [0]

    def reader() -> None:
        with open(out_file, "ab") as fh:
            while True:
                chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                fh.write(chunk)
                total[0] += len(chunk)
                if len(keep) < MAX_KEEP_BYTES:
                    keep.extend(chunk[:MAX_KEEP_BYTES - len(keep)])

    run = Running(label=label, started=t0)
    with _LOCK:
        _RUNNING[id(run)] = run
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    _nt.NtResumeProcess(handle)
    timed_out = stopped = False
    deadline = t0 + float(timeout_s)
    left: list[int] = []
    try:
        while proc.poll() is None:
            _note(seen, job.pids())
            if run.stop.is_set():
                stopped = True
                job.terminate(1)
                break
            if time.perf_counter() >= deadline:
                timed_out = True
                job.terminate(1)
                break
            time.sleep(POLL_S)
        _note(seen, job.pids())
        if not (timed_out or stopped) and job.pids():
            job.terminate(1)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            job.terminate(1)
            proc.wait(5)
        ms = (time.perf_counter() - t0) * 1000.0
        _note(seen, job.settle(5.0))
        left = _still_running(seen, 5.0)
        thread.join(10)
    finally:
        with _LOCK:
            _RUNNING.pop(id(run), None)
        job.close()
    return ProcResult(argv=list(argv), code=proc.returncode, ms=ms, timed_out=timed_out, stopped=stopped,
                      stop_reason=run.reason if stopped else "", output=bytes(keep), total_bytes=total[0],
                      pids_seen=sorted(seen), left_after=left, started_at=started_at)


def as_dict(r: ProcResult) -> dict[str, Any]:
    return {"argv": r.argv, "code": r.code, "ms": round(r.ms, 1), "timed_out": r.timed_out, "stopped": r.stopped,
            "stop_reason": r.stop_reason, "bytes": r.total_bytes, "pids_seen": r.pids_seen, "left_after": r.left_after}
