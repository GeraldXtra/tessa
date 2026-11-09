from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, MutableMapping

SECRET_NAME = re.compile(r"(API[_-]?KEY|_KEY(?:_|$)|^KEY_|TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|CREDENTIAL|PRIVATE)",
                         re.I)
SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
_HELD: dict[str, str] = {}
_LOCK = threading.Lock()


def configured_names() -> set[str]:
    try:
        import yaml

        brain = (yaml.safe_load(SETTINGS.read_text(encoding="utf-8")) or {}).get("brain") or {}
    except Exception:
        return set()
    return {str(v["api_key_env"]) for v in brain.values() if isinstance(v, dict) and v.get("api_key_env")}


def is_secret_name(name: str, configured: set[str] | None = None) -> bool:
    names = configured if configured is not None else configured_names()
    return bool(SECRET_NAME.search(str(name))) or str(name) in names


def secret_names(env: MutableMapping[str, str] | None = None) -> set[str]:
    source = os.environ if env is None else env
    configured = configured_names()
    return {n for n in source if is_secret_name(n, configured)}


def hold_and_scrub(env: MutableMapping[str, str] | None = None) -> list[str]:
    target = os.environ if env is None else env
    with _LOCK:
        names = sorted(secret_names(target), key=str.upper)
        for name in names:
            value = target.pop(name, None)
            if value is not None and name not in _HELD:
                _HELD[name] = value
        return names


def get(name: str) -> str:
    with _LOCK:
        held = _HELD.get(name)
    return held if held is not None else os.environ.get(name, "")


def source_of(name: str) -> str:
    with _LOCK:
        if name in _HELD:
            return "held in memory, taken from the environment at start"
    return "environment" if os.environ.get(name) else "none"


def held_names() -> list[str]:
    with _LOCK:
        return sorted(_HELD, key=str.upper)


def held_values() -> list[str]:
    with _LOCK:
        return [v for v in _HELD.values() if v]


def describe() -> dict[str, Any]:
    with _LOCK:
        return {"held": sorted(_HELD, key=str.upper), "lengths": {k: len(v) for k, v in _HELD.items()}}
