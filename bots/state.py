"""Tiny persistent memory (a JSON file kept between runs by the GitHub Actions cache).

Remembers which alerts were already sent and when each bot last ran, so a late or skipped
GitHub schedule never causes missed or duplicate alerts.
"""
import json
import os
import time

PATH = os.getenv("STATE_FILE", ".state/state.json")
KEEP_SECONDS = 60 * 24 * 3600
_state = None


def _load():
    global _state
    if _state is None:
        try:
            with open(PATH, encoding="utf-8") as f:
                _state = json.load(f)
        except (OSError, ValueError):
            _state = {}
        _state.setdefault("seen", {})
        _state.setdefault("last_run", {})
    return _state


def seen(key):
    return key in _load()["seen"]


def mark(key):
    _load()["seen"][key] = time.time()


def get(key, default=None):
    return _load().setdefault("kv", {}).get(key, default)


def put(key, value):
    _load().setdefault("kv", {})[key] = value


def last_run(job):
    return _load()["last_run"].get(job)


def set_last_run(job, value):
    _load()["last_run"][job] = value


def save():
    s = _load()
    cutoff = time.time() - KEEP_SECONDS
    s["seen"] = {k: v for k, v in s["seen"].items() if v > cutoff}
    os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(s, f)
