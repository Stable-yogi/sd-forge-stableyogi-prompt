"""
Sources are defined as data, not code — add or change a source by editing JSON, no Python changes.

Load order (later wins by id):
  1. providers.json        — shipped default source
  2. providers.local.json  — optional, git-ignored: local overrides

A source only needs a label, an address, and its subject list; the rest use sensible defaults.
"""

import base64
import dataclasses
import json
import os
from dataclasses import dataclass, field

_DIR = os.path.dirname(os.path.abspath(__file__))
_JSON = os.path.join(_DIR, "providers.json")
_LOCAL = os.path.join(_DIR, "providers.local.json")


@dataclass
class Provider:
    id: str
    label: str
    base_url: str = ""
    rating: str = "sfw"                 # passed through to the service
    auth_header: str = "X-API-Key"      # header the key is sent in
    modes: list = field(default_factory=list)   # subject fallback; refreshed at runtime
    default_quality: str = "ultra-detailed, photorealistic, sharp focus"
    default_style: str = "nl"           # "nl" or "tags"
    enabled: bool = True                # hidden from the UI when False
    requires_optin: bool = False        # True → user must confirm the age option first
    settings_prefix: str = ""           # opts key prefix; auto-derived from id if blank
    signup_url: str = "https://stableyogi.com"
    default_key: str = ""               # optional pre-filled key (git-ignored providers.local.json)

    def __post_init__(self):
        if not self.settings_prefix:
            self.settings_prefix = "syprompt_" + self.id.replace("-", "_")

    # opts keys (persisted in Forge settings) --------------------------------
    @property
    def key_opt(self):
        return self.settings_prefix + "_key"

    @property
    def url_opt(self):
        return self.settings_prefix + "_url"


_FALLBACK = {
    "id": "stableyogi-sfw",
    "label": "StableYogi Prompt Engine (SFW)",
    "base_url_b64": "aHR0cHM6Ly9zdGFibGV5b2dpLmNvbS9hcGkvcHJvbXB0LWVuZ2luZQ==",
    "rating": "sfw",
    "modes": ["Solo Female", "Solo Male", "Couple F+M", "Couple F+F", "Couple M+M"],
}


def _decode(raw):
    """Resolve an obfuscated endpoint (base_url_b64) so the plain URL isn't sitting in the repo."""
    if raw.get("base_url_b64") and not raw.get("base_url"):
        raw = dict(raw)
        try:
            raw["base_url"] = base64.b64decode(raw["base_url_b64"]).decode("utf-8").strip()
        except Exception:
            pass
    return raw


def _read(path):
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("providers", [])
    except Exception as e:  # never let a bad file take down the extension
        print(f"[stableyogi-prompt] could not read {os.path.basename(path)}: {e}")
        return []


def load_providers():
    """Return the merged provider list.

    Merge is **field-level** and keyed by id, so providers.local.json can override just one field
    (e.g. add `default_key` or a private `base_url`) without having to restate the whole provider.
    Unknown keys are dropped so a stray field never crashes the load.
    """
    valid = {f.name for f in dataclasses.fields(Provider)}
    raw_by_id = {}
    for raw in _read(_JSON) + _read(_LOCAL):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        raw = _decode(raw)                                  # resolve obfuscated endpoint
        clean = {k: v for k, v in raw.items() if k in valid}
        raw_by_id.setdefault(raw["id"], {}).update(clean)   # later (local) wins, per field

    out = []
    for pid, raw in raw_by_id.items():
        try:
            out.append(Provider(**raw))
        except Exception as e:
            print(f"[stableyogi-prompt] skipping bad provider {pid!r}: {e}")
    if not out:  # last-ditch default so the UI is never empty
        raw = _decode(_FALLBACK)
        out.append(Provider(**{k: v for k, v in raw.items() if k in valid}))
    return out


def usable(providers):
    """Providers safe to show in the dropdown: enabled and with a base_url configured."""
    out = [p for p in providers if p.enabled and (p.base_url or "").strip()]
    if not out:  # keep at least the SFW engine visible even if misconfigured
        out = [p for p in providers if p.rating == "sfw"] or providers[:1]
    return out
