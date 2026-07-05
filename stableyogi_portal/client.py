"""
Small HTTP helper for the StableYogi service.

Requests finished text and returns it. Every error becomes a short, friendly message so a
generation is never interrupted. Works for any configured source.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

try:
    import requests
    _HAVE_REQUESTS = True
except Exception:
    _HAVE_REQUESTS = False

TIMEOUT = 30

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


class Result:
    """Uniform return type. Truthy check via .ok; UI reads .message on failure."""

    def __init__(self, ok, prompts=None, remaining=None, quota=None, retry_after=0,
                 extra=None, error=None, message=""):
        self.ok = ok
        self.prompts = prompts or []
        self.remaining = remaining
        self.quota = quota or {}
        self.retry_after = retry_after
        self.extra = extra or {}
        self.error = error
        self.message = message


def _headers(provider, api_key):
    h = {"User-Agent": _UA, "Accept": "application/json"}
    if api_key:
        h[provider.auth_header] = api_key.strip()
    return h


def _friendly(status, body):
    reason = body.get("error", "") if isinstance(body, dict) else ""
    if status == 401:
        return "Invalid or missing API key — set it in Settings → StableYogi Prompt."
    if status == 403:
        if isinstance(body, str):     # a non-JSON body means an edge/access block, not a tier issue
            return "Couldn't reach the service — please update the extension."
        return reason or "This is a Pro feature — upgrade at stableyogi.com."
    if status == 429:
        return reason or "Please wait a moment before trying again."
    if status == 400:
        return reason or "Bad request."
    if status == 503:
        return "The service is temporarily unavailable — try again shortly."
    return reason or f"Request failed (HTTP {status})."


def _get(url, headers):
    """Return (status:int, body:dict|str|None). Raises only on a network-level failure."""
    if _HAVE_REQUESTS:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, (r.text or None)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, (raw or None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, (raw or None)


def _endpoint(provider, path, params):
    base = (provider.base_url or "").rstrip("/")
    return f"{base}/{path}?" + urllib.parse.urlencode(params)


def _retry_after(body):
    if isinstance(body, dict):
        try:
            return int(body.get("resetsInSec") or 0)
        except Exception:
            return 0
    return 0


def fetch_prompt(provider, api_key, subjects="Solo Female", count=1,
                 trigger="", quality="", style="nl", seed=None):
    """Request finished text. Returns a Result."""
    if not (provider.base_url or "").strip():
        return Result(False, error="not_configured", message=f"{provider.label} is not configured yet.")
    if not (api_key or "").strip():
        return Result(False, error="no_key", message="Enter your API key in Settings → StableYogi Prompt.")

    params = {
        "subjects": subjects,
        "count": max(1, min(int(count or 1), 50)),
        "style": style or "nl",
        "rating": provider.rating,
    }
    if (trigger or "").strip():
        params["trigger"] = trigger.strip()
    if (quality or "").strip():
        params["quality"] = quality.strip()
    if seed is not None and int(seed) >= 0:
        params["seed"] = int(seed)

    try:
        status, body = _get(_endpoint(provider, "prompt", params), _headers(provider, api_key))
    except Exception:
        return Result(False, error="network", message="Service unreachable — check your connection.")

    if status == 200 and isinstance(body, dict):
        return Result(True, prompts=body.get("prompts", []), remaining=body.get("remaining"),
                      quota=body.get("quota") or {}, extra=body)
    if status == 429:
        return Result(False, error="rate_limited", retry_after=_retry_after(body),
                      quota=(body.get("quota") if isinstance(body, dict) else {}) or {},
                      message=_friendly(status, body))
    if status == 400 and subjects != "Solo Female":
        return fetch_prompt(provider, api_key, "Solo Female", count, trigger, quality, style, seed)
    return Result(False, error=str(status), message=_friendly(status, body))


def fetch_modes(provider, api_key):
    """Request the available subjects for the current source."""
    fallback = {"modes": list(provider.modes or []), "modeList": []}
    if not (provider.base_url or "").strip():
        return Result(False, message="not configured", extra=fallback)
    try:
        status, body = _get(_endpoint(provider, "modes", {"rating": provider.rating}),
                            _headers(provider, api_key))
    except Exception:
        return Result(False, extra=fallback)
    if status == 200 and isinstance(body, dict) and body.get("modes"):
        return Result(True, extra={"modes": body["modes"], "modeList": body.get("modeList") or []})
    return Result(False, message=_friendly(status, body), extra=fallback)


def test_key(provider, api_key):
    """Check the account key and report a short status."""
    res = fetch_prompt(provider, api_key, count=1)
    if res.ok:
        return Result(True, message="✅ Pro key OK · " + quota_summary(res), extra=res.extra,
                      remaining=res.remaining, quota=res.quota)
    return res


def quota_summary(res):
    """Short status string for the panel."""
    q = res.quota or {}
    hr = (q.get("hourly") or {}).get("remaining")
    day = (q.get("daily") or {}).get("remaining")
    if hr is not None or day is not None:
        parts = []
        if hr is not None:
            parts.append(f"{hr}/hr")
        if day is not None:
            parts.append(f"{day}/day")
        return " · ".join(parts) + " left"
    if res.remaining is not None:
        return f"{res.remaining} left today"
    return "active"
