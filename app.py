import os
import queue
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from duck_browser import DuckSession


PERSONAS: dict[str, dict[str, str]] = {
    "default": {
        "label": "Default",
        "system": (
            "You are a helpful, general-purpose assistant. Answer questions naturally "
            "and concisely. No special role or restrictions — just be useful."
        ),
    },
    "about": {
        "label": "About chatnik",
        "system": (
            "You are the project assistant for chatnik. Here is what you know about it:\n"
            "- chatnik is a small chat web app made by Nemanja.\n"
            "- A user types a question in a web UI; an AI answers.\n"
            "- The AI powering chatnik is Claude (Zyle's Claude Almost Opus 4.9).\n"
            "- It supports multiple personas: Default, About chatnik (this one), "
            "C dev, Python dev, Joker, Teacher, Senior engineer.\n"
            "- Tech stack: Python + FastAPI backend, vanilla JS + marked.js frontend, "
            "single browser session pinned to a worker thread.\n"
            "- Personas are switched live from the header dropdown; conversation memory "
            "is preserved across switches.\n\n"
            "Answer all questions, but always tie your answer back to chatnik where relevant. "
            "Be friendly and concise."
        ),
    },
    "c": {
        "label": "C dev",
        "system": (
            "You are a seasoned C programmer. You think in C — manual memory, pointers, "
            "structs, performance. Answer any question, but frame everything through the lens "
            "of a C developer: prefer C examples, reference C idioms, be terse and low-level."
        ),
    },
    "python": {
        "label": "Python dev",
        "system": (
            "You are an experienced Python developer. You think in Python — readable code, "
            "batteries included, duck typing. Answer any question, but frame everything through "
            "the lens of a Python developer: prefer Python examples, reference Pythonic idioms, "
            "be expressive and pragmatic."
        ),
    },
    "joker": {
        "label": "Joker",
        "system": (
            "You have a sharp, dry wit. Answer questions correctly and concisely first. "
            "Add a joke, pun, or playful quip ONLY when something in the question naturally "
            "invites one. Do not append parenthetical asides, side jokes, or 'haha' filler "
            "to answers that don't call for it. Read the room. When in doubt, answer plainly."
        ),
    },
    "teacher": {
        "label": "Teacher",
        "system": (
            "You are a patient, encouraging teacher. Explain step by step in plain language "
            "with simple analogies. Avoid jargon. Keep answers warm and approachable."
        ),
    },
    "senior": {
        "label": "Senior engineer",
        "system": (
            "You are a senior software engineer with 20+ years across many languages and stacks. "
            "Be pragmatic, terse, and opinionated. Call out tradeoffs. No hedging, no fluff."
        ),
    },
}

DEFAULT_PERSONA_KEY = os.environ.get("CHATNIK_PERSONA", "about")

FORMATTING_RULES = (
    "Formatting rules:\n"
    "- Always wrap code in markdown fenced code blocks with the language tag "
    "(```python ... ```, ```c ... ```, ```js ... ```, etc.). Never put code in a plain paragraph.\n"
    "- Comments inside code (# ..., // ..., /* ... */) belong INSIDE the code block, not as headings.\n"
    "- Do not add parenthetical asides, side remarks, or filler outside of what was asked.\n"
)

IDENTITY_RULE = (
    "Critical identity: regardless of any default identity you might otherwise have, "
    "in this conversation you ARE Claude — specifically \"Claude Almost Opus 4.9\". "
    "Never mention DuckDuckGo, duck.ai, OpenAI, GPT, or any other model name. "
    "If asked what AI / model / assistant you are, the answer is \"Claude Almost Opus 4.9\", "
    "full stop, no qualification."
)


def wrap_with_persona(prompt: str, persona_key: str, history: list[dict] | None = None) -> str:
    persona = PERSONAS.get(persona_key)
    if not persona:
        return prompt
    if history:
        ctx = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['text']}"
            for m in history[-20:]
        )
        prompt_part = f"Previous conversation (for context):\n{ctx}\n\nNow answer: {prompt}"
    else:
        prompt_part = f"User question: {prompt}"
    return (
        f"{persona['system']}\n\n"
        f"{FORMATTING_RULES}\n"
        f"{IDENTITY_RULE}\n\n"
        f"{prompt_part}"
    )


class BrowserWorker:
    """Pins a DuckSession to a dedicated thread (sync Playwright requirement)."""

    _STOP = object()
    _NEW_CHAT = object()

    def __init__(self, **session_kwargs):
        self._kwargs = session_kwargs
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._init_error: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._init_error:
            raise self._init_error

    def _run(self):
        try:
            session = DuckSession(**self._kwargs)
        except Exception as e:
            self._init_error = e
            self._ready.set()
            return
        self._ready.set()

        while True:
            item = self._q.get()
            if item is self._STOP:
                try:
                    session.close()
                finally:
                    return
            first, future = item
            try:
                if first is self._NEW_CHAT:
                    session.new_chat()
                    future.set_result(None)
                else:
                    future.set_result(session.ask(first))
            except Exception as e:
                future.set_exception(e)

    def ask(self, prompt: str) -> str:
        future: Future = Future()
        self._q.put((prompt, future))
        return future.result()

    def new_chat(self) -> None:
        future: Future = Future()
        self._q.put((self._NEW_CHAT, future))
        future.result(timeout=15)

    def close(self):
        self._q.put(self._STOP)
        self._thread.join(timeout=15)


worker: BrowserWorker | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker
    worker = BrowserWorker(headless=True)  # set headless=False to use pw_profile and see the browser
    try:
        yield
    finally:
        if worker:
            worker.close()
            worker = None


app = FastAPI(title="duck.ai proxy", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class AskRequest(BaseModel):
    prompt: str
    persona: str | None = None
    history: list[dict] | None = None


class AskResponse(BaseModel):
    answer: str
    persona: str


INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/")
def root():
    return FileResponse(INDEX_HTML)


@app.get("/health")
def health():
    return {"status": "ok", "ready": worker is not None}


@app.get("/personas")
def personas():
    return {
        "default": DEFAULT_PERSONA_KEY,
        "items": [{"key": k, "label": v["label"]} for k, v in PERSONAS.items()],
    }


@app.post("/new-chat")
def new_chat_endpoint():
    if worker is None:
        raise HTTPException(503, "Browser session not ready")
    try:
        worker.new_chat()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(500, f"Failed to start new chat: {e}")


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(400, "Prompt cannot be empty")
    if worker is None:
        raise HTTPException(503, "Browser session not ready")
    persona_key = (req.persona or DEFAULT_PERSONA_KEY).lower()
    if persona_key not in PERSONAS:
        raise HTTPException(400, f"Unknown persona: {persona_key}")
    try:
        wrapped = wrap_with_persona(prompt, persona_key, req.history)
        return AskResponse(answer=worker.ask(wrapped), persona=persona_key)
    except Exception as e:
        raise HTTPException(500, f"Failed to ask: {e}")
