"""
StableYogi Prompt — brings ready-made prompts from your StableYogi account into Forge.

A collapsible panel in txt2img/img2img with a subject picker, trigger/quality controls, a
"Get prompt" button that fills the prompt box, and an optional "auto on every generation" toggle.
Everything is opt-in and only affects the prompt text; a service hiccup never interrupts a run.
"""

import dataclasses
import os
import sys

import gradio as gr

_EXT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EXT not in sys.path:                     # so `stableyogi_portal` imports regardless of load order
    sys.path.insert(0, _EXT)

from stableyogi_portal import buffer as buf
from stableyogi_portal import client as api
from stableyogi_portal import providers as prov

from modules import scripts, shared, script_callbacks

PROVIDERS = prov.load_providers()
_USABLE = prov.usable(PROVIDERS)
_LABELS = [p.label for p in _USABLE]
_HISTORY = []                                 # last-fetched prompts (session-only), newest first
_HISTORY_MAX = 12

# Auto toggle, mirrored by the checkbox at the prompt box and the in-panel one (either turns it on).
# Items are served from a small local cache (stableyogi_portal.buffer).
_AUTO = {"on": False}

# JS: push preview text into the positive-prompt textarea (stable elem_id → version-proof).
_INJECT_JS = """
function(text, mode){
  if(!text){ return []; }
  var root = (typeof gradioApp === 'function') ? gradioApp() : document;
  var ta = root.querySelector('#txt2img_prompt textarea') || root.querySelector('#img2img_prompt textarea');
  if(ta){
    if(mode === 'Append to prompt' && ta.value.trim()){ ta.value = ta.value.trim() + ', ' + text; }
    else { ta.value = text; }
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  return [];
}
"""


def _provider(label):
    for p in _USABLE:
        if p.label == label:
            return p
    return _USABLE[0] if _USABLE else prov.load_providers()[0]


def _live(p):
    """Provider with base_url/key resolved from persisted settings (Settings tab overrides JSON)."""
    url = (getattr(shared.opts, p.url_opt, "") or "").strip() or p.base_url
    return dataclasses.replace(p, base_url=url)


def _key(p):
    """Saved key (Forge settings) if present, else the pre-loaded providers.local.json default."""
    saved = (getattr(shared.opts, p.key_opt, "") or "").strip()
    return saved or (p.default_key or "").strip()


def _persist_key(p, key):
    """Save a key into Forge settings so it survives restarts and auto-fetch can read it."""
    try:
        shared.opts.set(p.key_opt, (key or "").strip())
        shared.opts.save(shared.config_filename)
        return True
    except Exception as e:
        print(f"[stableyogi-prompt] could not save key: {e}")
        return False


def _optin_ok(p):
    """Age-restricted sources require the corresponding option in Settings first."""
    if not p.requires_optin:
        return True
    return bool(getattr(shared.opts, "syprompt_adult_optin", False))


def _remember(text):
    if text and text not in _HISTORY:
        _HISTORY.insert(0, text)
        del _HISTORY[_HISTORY_MAX:]


class StableYogiPrompt(scripts.Script):
    def title(self):
        return "StableYogi Prompt"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    # ---- UI ----------------------------------------------------------------
    def ui(self, is_img2img):
        default_quality = getattr(shared.opts, "syprompt_default_quality",
                                  _USABLE[0].default_quality if _USABLE else "")
        default_style = getattr(shared.opts, "syprompt_default_style",
                                _USABLE[0].default_style if _USABLE else "nl")
        first = _USABLE[0] if _USABLE else None
        modes = list(first.modes) if first and first.modes else ["Solo Female"]

        with gr.Accordion("StableYogi Prompt", open=False, elem_classes=["syprompt-accordion"]):
            gr.Markdown(
                "Curated, ready-made prompts from your **StableYogi Pro** account — all controls are "
                "right here. Get your key at [stableyogi.com](https://stableyogi.com) → "
                "Settings → Prompt Engine API."
            )
            with gr.Row():
                api_key = gr.Textbox(label="API key (Pro)", type="password", placeholder="sy-...",
                                     value=(_key(_USABLE[0]) if _USABLE else ""), scale=4)
                save_key_btn = gr.Button("💾 Save key", scale=0, min_width=90)
            with gr.Row():
                provider_dd = gr.Dropdown(label="Source", choices=_LABELS,
                                          value=(_LABELS[0] if _LABELS else None), scale=3)
                subjects = gr.Dropdown(label="Subject", choices=modes,
                                       value=(modes[0] if modes else None), scale=2)
                refresh_modes = gr.Button("🔄", scale=0, min_width=40)
            trigger = gr.Textbox(label="Trigger / LoRA word", placeholder="e.g. your trigger word")
            with gr.Row():
                quality = gr.Textbox(label="Quality suffix", value=default_quality, scale=3)
                style = gr.Radio(label="Style", choices=["nl", "tags"], value=default_style, scale=1)
            with gr.Row():
                get_btn = gr.Button("🎲 Get prompt", variant="primary", scale=2)
                test_btn = gr.Button("🔑 Test key", scale=1)
            preview = gr.Textbox(label="Fetched prompt (editable)", lines=3, show_copy_button=True)
            with gr.Row():
                inject_mode = gr.Radio(label="", choices=["Replace prompt", "Append to prompt"],
                                       value="Replace prompt", scale=3)
                send_btn = gr.Button("⬆ Send to prompt box", scale=1)
            status = gr.Markdown("")
            with gr.Row():
                history = gr.Dropdown(label="History (this session)", choices=[], value=None, scale=4)
            autofetch = gr.Checkbox(
                label="✨ Auto-fetch on every generation  (same as the toggle by the Prompt box above)",
                value=False,
            )

        # --- wiring -------------------------------------------------------
        def _use_key(p, key_v):
            """Prefer the in-panel key; persist it so it sticks + auto-fetch can use it."""
            key = (key_v or "").strip()
            if key and key != _key(p):
                _persist_key(p, key)
            return key or _key(p)

        def _do_fetch(provider_label, key_v, subjects_v, trigger_v, quality_v, style_v):
            p = _live(_provider(provider_label))
            key = _use_key(p, key_v)
            if not _optin_ok(p):
                return "", "⚠️ Enable the age-restricted option in Settings → StableYogi Prompt first.", gr.update()
            prompt, msg = buf.serve_one(p, key, subjects_v, trigger_v, quality_v, style_v)
            if prompt:
                _remember(prompt)
                return prompt, f"✅ {p.label} · {msg}", gr.update(choices=list(_HISTORY))
            return "", f"⚠️ {msg}", gr.update()

        def _do_test(provider_label, key_v):
            p = _live(_provider(provider_label))
            res = api.test_key(p, _use_key(p, key_v))
            return res.message if res.message else ("✅ OK" if res.ok else "⚠️ Failed")

        def _do_refresh_modes(provider_label, key_v):
            p = _live(_provider(provider_label))
            res = api.fetch_modes(p, _use_key(p, key_v))
            ml = res.extra.get("modeList") or []
            modes = res.extra.get("modes") or (p.modes or ["Solo Female"])
            if ml:  # (label, id) tuples for pretty labels; the id is what gets sent to the portal
                choices = [(m.get("label") or m.get("id"), m.get("id")) for m in ml if m.get("id")]
                first = choices[0][1] if choices else None
            else:
                choices = list(modes)
                first = choices[0] if choices else None
            return gr.update(choices=choices, value=first)

        def _do_save_key(provider_label, key_v):
            p = _provider(provider_label)
            ok = _persist_key(p, key_v)
            return "💾 Key saved." if ok else "⚠️ Could not save key."

        def _on_provider(provider_label):
            p = _provider(provider_label)
            m = p.modes or ["Solo Female"]
            return _key(p), gr.update(choices=m, value=(m[0] if m else None))

        get_btn.click(_do_fetch,
                      inputs=[provider_dd, api_key, subjects, trigger, quality, style],
                      outputs=[preview, status, history]).then(
                      fn=None, js=_INJECT_JS, inputs=[preview, inject_mode], outputs=[])
        send_btn.click(fn=None, js=_INJECT_JS, inputs=[preview, inject_mode], outputs=[])
        test_btn.click(_do_test, inputs=[provider_dd, api_key], outputs=[status])
        refresh_modes.click(_do_refresh_modes, inputs=[provider_dd, api_key], outputs=[subjects])
        save_key_btn.click(_do_save_key, inputs=[provider_dd, api_key], outputs=[status])
        provider_dd.change(_on_provider, inputs=[provider_dd], outputs=[api_key, subjects])
        history.change(fn=lambda h: h or "", inputs=[history], outputs=[preview]).then(
                      fn=None, js=_INJECT_JS, inputs=[preview, inject_mode], outputs=[])

        # Order here MUST match the process_batch signature below.
        return [autofetch, provider_dd, subjects, trigger, quality, style, inject_mode]

    def process_batch(self, p, autofetch, provider_label, subjects, trigger, quality, style,
                      inject_mode, **kwargs):
        if not (autofetch or _AUTO["on"]):   # either toggle turns it on
            return
        prompts = kwargs.get("prompts")
        if not prompts:
            return
        try:
            provider = _live(_provider(provider_label))
            if not _optin_ok(provider):
                print("[stableyogi-prompt] auto-fetch off: age option not enabled.")
                return
            n = len(prompts)
            fetched, msg = buf.serve_many(n, provider, _key(provider), subjects, trigger, quality, style)
            if not fetched:
                print(f"[stableyogi-prompt] auto-fetch skipped: {msg}")
                return
            fetched = (list(fetched) * ((n // len(fetched)) + 1))[:n]   # pad if fewer than n came back
            batch_no = kwargs.get("batch_number", 0)
            for i in range(n):
                new = fetched[i]
                if inject_mode == "Append to prompt" and prompts[i].strip():
                    new = prompts[i].strip() + ", " + new
                prompts[i] = new
                idx = batch_no * n + i                  # keep saved metadata in sync
                if isinstance(getattr(p, "all_prompts", None), list) and idx < len(p.all_prompts):
                    p.all_prompts[idx] = new
            _remember(fetched[0])
            p.extra_generation_params["StableYogi Prompt"] = f"{provider.label} · {subjects}"
        except Exception as e:                          # never take down a generation
            print(f"[stableyogi-prompt] auto-fetch error (ignored): {e}")


# ---- persisted settings (Settings → StableYogi Prompt) --------------------
def on_ui_settings():
    section = ("stableyogi_prompt", "StableYogi Prompt")
    opts = shared.opts
    try:
        opts.add_option("syprompt_adult_optin", shared.OptionInfo(
            False, "Allow age-restricted sources (18+)", section=section))
        for pv in PROVIDERS:
            opts.add_option(pv.key_opt, shared.OptionInfo(
                "", f"{pv.label} — API key", section=section))
        opts.add_option("syprompt_default_quality", shared.OptionInfo(
            _USABLE[0].default_quality if _USABLE else "ultra-detailed, photorealistic, sharp focus",
            "Default quality suffix", section=section))
        opts.add_option("syprompt_default_style", shared.OptionInfo(
            _USABLE[0].default_style if _USABLE else "nl", "Default quality style",
            gr.Radio, {"choices": ["nl", "tags"]}, section=section))
    except Exception as e:
        print(f"[stableyogi-prompt] could not register settings: {e}")


script_callbacks.on_ui_settings(on_ui_settings)


# ---- inject the "auto-fetch on Generate" checkbox right at the prompt box ---
def on_after_component(component, **kwargs):
    """Render the master auto-fetch toggle as a small pill inside the prompt box's corner.

    Hooks the prompt textbox so the checkbox becomes a sibling inside the prompt row; style.css
    then absolutely-positions it in the bottom-left corner so it costs no vertical space.
    txt2img + img2img.

    Fully defensive: if the layout ever changes, this degrades to a no-op (the in-panel checkbox
    still works), never breaking the UI.
    """
    try:
        elem_id = getattr(component, "elem_id", None)
        if elem_id not in ("txt2img_prompt", "img2img_prompt"):
            return
        chk = gr.Checkbox(
            label="Auto-prompt",
            value=_AUTO["on"],
            elem_id=f"syprompt_auto_{elem_id}",
            elem_classes=["syprompt-auto-toggle"],
        )
        chk.change(fn=lambda v: _AUTO.__setitem__("on", bool(v)), inputs=[chk], outputs=[])
    except Exception as e:
        print(f"[stableyogi-prompt] prompt-box toggle inject skipped: {e}")


script_callbacks.on_after_component(on_after_component)
