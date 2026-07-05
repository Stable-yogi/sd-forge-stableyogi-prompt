"""
Keeps a small local cache of ready-to-use items per selection, so repeat requests are served
locally instead of asking the service each time. The cache is capped and drains as items are used,
refilling quietly in the background when it runs low. Persisted under .cache so it survives restarts.
"""

import json
import os
import threading
import time

from . import client

BATCH = 25          # items requested per call
THRESHOLD = 5       # refill in the background once fewer than this remain
CAP = 50            # never hold more than this per selection
MIN_BACKOFF = 30    # seconds to wait before retrying after a busy signal

_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE = os.path.join(_DIR, ".cache")
_FILE = os.path.join(_CACHE, "prompt_buffers.json")

_LOCK = threading.Lock()
_BUFFERS = {}


def _now():
    return time.time()


def _new():
    return {"items": [], "backoff_until": 0.0, "quota": {}, "filling": False}


def _load():
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for sig, e in data.items():
            _BUFFERS[sig] = {"items": list(e.get("items", []))[:CAP],
                             "backoff_until": float(e.get("backoff_until", 0) or 0),
                             "quota": e.get("quota") or {}, "filling": False}
    except Exception:
        pass


def _save():
    """Persist under the lock's protection (callers already hold _LOCK)."""
    try:
        os.makedirs(_CACHE, exist_ok=True)
        dump = {sig: {"items": e["items"], "backoff_until": e["backoff_until"], "quota": e["quota"]}
                for sig, e in _BUFFERS.items()}
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dump, f)
        os.replace(tmp, _FILE)
    except Exception:
        pass


_load()


def signature(provider, subjects, trigger, quality, style):
    return "\x1f".join([getattr(provider, "id", "?"), subjects or "", (trigger or "").strip(),
                        (quality or "").strip(), style or "nl"])


def status(sig):
    """Short status line for the panel, e.g. 'ready 18 · 3400/day left'."""
    with _LOCK:
        e = _BUFFERS.get(sig) or _new()
        n = len(e["items"])
        q = e.get("quota") or {}
    hr = (q.get("hourly") or {}).get("remaining")
    day = (q.get("daily") or {}).get("remaining")
    tail = " · ".join([f"{hr}/hr" for _ in [0] if hr is not None] +
                      [f"{day}/day" for _ in [0] if day is not None])
    return f"ready {n}" + (f" · {tail} left" if tail else "")


def _refill(sig, provider, api_key, subjects, trigger, quality, style):
    """Request a batch (network happens OUTSIDE the lock). Returns the client.Result."""
    res = client.fetch_prompt(provider, api_key, subjects, BATCH, trigger, quality, style, seed=-1)
    with _LOCK:
        e = _BUFFERS.setdefault(sig, _new())
        if res.ok and res.prompts:
            e["items"].extend(res.prompts)
            e["items"] = e["items"][:CAP]
            e["quota"] = res.quota or e.get("quota", {})
            e["backoff_until"] = 0.0
        elif res.error == "rate_limited":
            e["backoff_until"] = _now() + max(int(res.retry_after or 0), MIN_BACKOFF)
            if res.quota:
                e["quota"] = res.quota
        e["filling"] = False
        _save()
    return res


def _maybe_bg_refill(sig, provider, api_key, subjects, trigger, quality, style):
    with _LOCK:
        e = _BUFFERS.setdefault(sig, _new())
        if len(e["items"]) >= THRESHOLD or e["filling"] or e["backoff_until"] > _now():
            return
        e["filling"] = True
    threading.Thread(target=_refill, daemon=True,
                     args=(sig, provider, api_key, subjects, trigger, quality, style)).start()


def serve_one(provider, api_key, subjects, trigger, quality, style):
    """Return (item:str|None, message:str). Serves locally; refills in the background."""
    sig = signature(provider, subjects, trigger, quality, style)
    with _LOCK:
        e = _BUFFERS.setdefault(sig, _new())
        item = e["items"].pop(0) if e["items"] else None
        backing_off = e["backoff_until"] > _now()
        if item is not None:
            _save()

    if item is None:
        if backing_off:
            return None, "⏳ Please wait a moment before trying again."
        res = _refill(sig, provider, api_key, subjects, trigger, quality, style)
        with _LOCK:
            e = _BUFFERS[sig]
            item = e["items"].pop(0) if e["items"] else None
            if item is not None:
                _save()
        if item is None:
            return None, (res.message or "Nothing available right now.")
        return item, status(sig)

    _maybe_bg_refill(sig, provider, api_key, subjects, trigger, quality, style)
    return item, status(sig)


def serve_many(n, provider, api_key, subjects, trigger, quality, style):
    """Return (list, message). Tops up locally if the cache can't cover n."""
    sig = signature(provider, subjects, trigger, quality, style)
    out = []
    with _LOCK:
        e = _BUFFERS.setdefault(sig, _new())
        while e["items"] and len(out) < n:
            out.append(e["items"].pop(0))
        backing_off = e["backoff_until"] > _now()
        if out:
            _save()

    tries = 0
    while len(out) < n and not backing_off and tries < 3:
        tries += 1
        res = _refill(sig, provider, api_key, subjects, trigger, quality, style)
        with _LOCK:
            e = _BUFFERS[sig]
            while e["items"] and len(out) < n:
                out.append(e["items"].pop(0))
            backing_off = e["backoff_until"] > _now()
            if out:
                _save()
        if not res.ok and res.error != "rate_limited":
            break

    _maybe_bg_refill(sig, provider, api_key, subjects, trigger, quality, style)
    msg = status(sig) if out else "Nothing available right now."
    return out, msg
