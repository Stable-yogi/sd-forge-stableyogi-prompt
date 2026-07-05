# sd-forge-stableyogi-prompt

Brings ready-made prompts from your **StableYogi** account into **Forge / Automatic1111**. Paste your
key, pick a subject, and drop a finished prompt into your prompt box — or let it fill in
automatically on each generation.

Built by [stableyogi.com](https://stableyogi.com). Requires an account key (Settings → Prompt Engine API).

## Install
1. Copy `sd-forge-stableyogi-prompt` into your Forge `extensions/` folder.
2. Restart Forge.
3. Open **txt2img** → the **StableYogi Prompt** panel → paste your key in the **API key** box →
   **Save key**.

## Using it
- **Get prompt** — drops a ready-made prompt into the box.
- **Subject** — pick who the prompt is about (refresh the list with 🔄).
- **Trigger / Quality / Style** — prepend your LoRA word, add a quality suffix, choose sentence (`nl`)
  or tag (`tags`) style.
- **Auto-fetch on each generation** — a small toggle at the prompt box; when on, every generation
  gets a fresh prompt (one per image in a batch). Replace or append to what you've typed.
- **History** — reuse anything fetched this session.

## Notes
- Your key stays on your machine and is never written into image metadata.
- Messages show inline — **a generation is never interrupted**.
- No extra Python packages required.
