"""
Fetches ready-to-use items from the service on demand — one API call per request, asking for
exactly the number of items needed (1 for a single prompt, N for a batch of N). A user's allowance
is therefore spent 1:1 with the prompts they actually use: no bulk pre-fetch, no surplus to waste,
no stale local copy. A short backoff is honoured after a busy signal.
"""

import threading
import time

from . import client

MIN_BACKOFF = 30    # seconds to wait before retrying after a busy signal
MAX_PER_CALL = 50   # the service serves at most this many per call

_LOCK = threading.Lock()
_STATE = {}         # signature -> {"backoff_until": float, "quota": dict}


def _now():
    return time.time()


def signature(provider, subjects, trigger, quality, style):
    return "\x1f".join([getattr(provider, "id", "?"), subjects or "", (trigger or "").strip(),
                        (quality or "").strip(), style or "nl"])


def clear():
    """Drop transient state (backoff/quota). Nothing is cached locally, so there are no stored
    prompts to purge — kept for API compatibility with the panel's Refresh button."""
    with _LOCK:
        _STATE.clear()
    return 0


def status(sig):
    """Short status line for the panel, e.g. 'ready · 28/hr left'."""
    with _LOCK:
        q = (_STATE.get(sig) or {}).get("quota") or {}
    hr = (q.get("hourly") or {}).get("remaining")
    day = (q.get("daily") or {}).get("remaining")
    tail = " · ".join([f"{hr}/hr" for _ in [0] if hr is not None] +
                      [f"{day}/day" for _ in [0] if day is not None])
    return "ready" + (f" · {tail} left" if tail else "")


def _backing_off(sig):
    with _LOCK:
        return (_STATE.get(sig, {}).get("backoff_until", 0.0)) > _now()


def _fetch(sig, provider, api_key, subjects, trigger, quality, style, count):
    """One API call for exactly `count` items (network happens outside the lock). Returns Result."""
    res = client.fetch_prompt(provider, api_key, subjects, count, trigger, quality, style, seed=-1)
    with _LOCK:
        st = _STATE.setdefault(sig, {"backoff_until": 0.0, "quota": {}})
        if res.ok:
            st["quota"] = res.quota or st.get("quota", {})
            st["backoff_until"] = 0.0
        elif res.error == "rate_limited":
            st["backoff_until"] = _now() + max(int(res.retry_after or 0), MIN_BACKOFF)
            if res.quota:
                st["quota"] = res.quota
    return res


def serve_one(provider, api_key, subjects, trigger, quality, style):
    """Return (item:str|None, message:str). One API call, one item, one unit of quota."""
    sig = signature(provider, subjects, trigger, quality, style)
    if _backing_off(sig):
        return None, "⏳ Please wait a moment before trying again."
    res = _fetch(sig, provider, api_key, subjects, trigger, quality, style, 1)
    if res.ok and res.prompts:
        return res.prompts[0], status(sig)
    return None, (res.message or "Nothing available right now.")


def serve_many(n, provider, api_key, subjects, trigger, quality, style):
    """Return (list, message). One API call for exactly `n` items — a batch of n costs n of quota."""
    n = max(1, int(n))
    sig = signature(provider, subjects, trigger, quality, style)
    if _backing_off(sig):
        return [], "⏳ Please wait a moment before trying again."
    res = _fetch(sig, provider, api_key, subjects, trigger, quality, style, min(n, MAX_PER_CALL))
    if res.ok and res.prompts:
        out = list(res.prompts)
        if 0 < len(out) < n:                       # batch bigger than one call can serve → cycle
            out = (out * ((n // len(out)) + 1))[:n]
        return out[:n], status(sig)
    return [], (res.message or "Nothing available right now.")
