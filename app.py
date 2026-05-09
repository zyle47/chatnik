import queue
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from duck_browser import DuckSession


class BrowserWorker:
    """Pins a DuckSession to a dedicated thread (sync Playwright requirement)."""

    _STOP = object()

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
            prompt, future = item
            try:
                future.set_result(session.ask(prompt))
            except Exception as e:
                future.set_exception(e)

    def ask(self, prompt: str) -> str:
        future: Future = Future()
        self._q.put((prompt, future))
        return future.result()

    def close(self):
        self._q.put(self._STOP)
        self._thread.join(timeout=15)


worker: BrowserWorker | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker
    worker = BrowserWorker(headless=True)
    try:
        yield
    finally:
        if worker:
            worker.close()
            worker = None


app = FastAPI(title="duck.ai proxy", lifespan=lifespan)


class AskRequest(BaseModel):
    prompt: str


class AskResponse(BaseModel):
    answer: str


INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/")
def root():
    return FileResponse(INDEX_HTML)


@app.get("/health")
def health():
    return {"status": "ok", "ready": worker is not None}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(400, "Prompt cannot be empty")
    if worker is None:
        raise HTTPException(503, "Browser session not ready")
    try:
        return AskResponse(answer=worker.ask(prompt))
    except Exception as e:
        raise HTTPException(500, f"Failed to ask: {e}")
