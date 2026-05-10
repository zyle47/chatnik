# chatnik

A small headless-Firefox bridge to [duck.ai](https://duck.ai), wrapped behind FastAPI with a tiny chat UI. The browser opens duck.ai once at startup, and every prompt is typed into the chat textarea, scraped back, and rendered as Markdown. Multiple personas are supported.

## What's in here

- `duck_browser.py` — Playwright session that drives duck.ai (typing, waiting, scraping)
- `app.py` — FastAPI service: `/ask`, `/personas`, `/health`, plus serves the UI
- `static/index.html` — the chat UI (vanilla JS + `marked`)
- `script.py` — CLI version (REPL) for quick testing without a browser
- `requirements.txt` — Python deps

## Setup on a fresh machine

### 1. System requirements

- Python 3.10+
- Linux/macOS/Windows
- ~300 MB free for Playwright's bundled Firefox

### 2. Clone and create a venv

```bash
git clone <this repo> chatnik2
cd chatnik2
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
playwright install firefox
```

(Optional: `playwright install chromium` if you want to try the `--browser chromium` CLI flag.)

### 4. First run — solve the CAPTCHA (if duck.ai challenges you)

Duck.ai usually lets Playwright through without issues. But if you hit a Cloudflare CAPTCHA on the first run, do this once:

**In [app.py](app.py), line ~187, temporarily change:**
```python
worker = BrowserWorker(headless=True)   # normal
```
**to:**
```python
worker = BrowserWorker(headless=False)  # visible browser — solve CAPTCHA here
```

Then start the server, solve the CAPTCHA in the browser window that pops up, and close the window. A `pw_profile/` folder is created next to `app.py` — this saves the session so you won't be challenged again.

**Change the line back to `headless=True`** and restart. From now on, if `pw_profile/` exists, it gets reused automatically — no more CAPTCHA.

> `pw_profile/` is in `.gitignore`. Don't commit it.

### 5. Run the API + UI

```bash
uvicorn app:app --port 8000
```

Then open `http://localhost:8000` in any browser. Pick a persona from the dropdown, ask away.

- `/` — chat UI
- `/ask` — POST `{"prompt": "...", "persona": "c"}` → JSON answer
- `/personas` — list available personas
- `/health` — liveness check
- `/docs` — auto-generated Swagger UI

## CLI mode

The original interactive script still works without uvicorn:

```bash
python script.py                                  # REPL, headless
python script.py --show "what's a pointer?"       # visible browser, one-shot prompt
python script.py --profile --show                 # use real Firefox profile copy
python script.py --browser chromium "..."         # try Chromium instead
python script.py --debug                          # dump page HTML to ./debug/ each turn
```

## Personas

Defined in `app.py` under `PERSONAS`. Add a new one by adding a key to the dict — the UI dropdown picks it up from `/personas` automatically. Default persona can be set with:

```bash
CHATNIK_PERSONA=joker uvicorn app:app
```

Available out of the box: `c`, `python`, `joker`, `teacher`, `senior`.

## Operational notes

- **One browser, one in-flight question.** The FastAPI app pins a single `DuckSession` to a dedicated worker thread (sync Playwright requirement) and serializes requests through a queue. Concurrent requests just wait their turn.
- **Don't use `--reload`** in production — uvicorn restarts re-launch Firefox.
- **Don't use `--workers > 1`** — each worker would try to spin up its own browser.
- **Selectors can break.** Duck.ai changes their CSS hashes occasionally. The current scrape anchors on `.space-y-4.whitespace-normal` (the assistant prose body) and `<pre><code class="language-...">` (code blocks). If output suddenly goes blank, run `python script.py --show --debug` and inspect `./debug/turnNN.html` to find the new selector.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `BrowserType.launch: Executable doesn't exist` | Run `playwright install firefox` |
| Cloudflare CAPTCHA loop on duck.ai | Use `--profile` (CLI) or call the persistent-context path; close your real Firefox first |
| Empty answer / `[no selector matched]` | Duck.ai changed the DOM. Re-run with `--debug` and update selectors in `duck_browser.py` |
| Code blocks render as paragraphs | The persona didn't follow the formatting rules — usually clears up after a couple of turns; or click duck.ai's "New Chat" mentally and reload |
| CAPTCHA keeps appearing after first run | Make sure `pw_profile/` exists (created on first `headless=False` run); don't delete it |
