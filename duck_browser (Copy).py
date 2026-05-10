import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

URL = "https://duck.ai/"
ANSWER_WAIT_MS = 12_000
DEBUG_DIR = Path(__file__).parent / "debug"
PROFILE_DIR = Path(__file__).parent / "pw_profile"


def _ensure_profile_dir() -> str:
    PROFILE_DIR.mkdir(exist_ok=True)
    return str(PROFILE_DIR)


def _release_profile_locks():
    for name in ("lock", "parent.lock", ".parentlock"):
        (PROFILE_DIR / name).unlink(missing_ok=True)


def _click_if_present(page, *labels, timeout=1500):
    for label in labels:
        try:
            btn = page.get_by_role("button", name=label, exact=False)
            if btn.count() and btn.first.is_visible(timeout=timeout):
                btn.first.click()
                page.wait_for_timeout(400)
                return True
        except Exception:
            continue
    return False


_TO_MARKDOWN_JS = r"""
(el) => {
    const KNOWN_LANGS = new Set([
        'python','py','javascript','js','typescript','ts','html','css','json',
        'bash','sh','shell','sql','java','c','cpp','c++','csharp','cs','go',
        'rust','rs','ruby','rb','php','swift','kotlin','scala','r','yaml',
        'yml','xml','markdown','md','dockerfile','plaintext','text',
    ]);

    function nodeToMd(node) {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent;
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        const tag = node.tagName.toLowerCase();
        if (tag === 'script' || tag === 'style' || tag === 'svg') return '';

        // Skip the model-label heading duck.ai renders above each response
        if (node.id && node.id.startsWith('heading-')) return '';
        // Skip citation widgets in-place (no DOM mutation)
        if (tag === 'button') return '';
        if (tag === 'a' && node.hasAttribute('aria-label')) return '';

        // <pre> = code block. innerText (not textContent) preserves the visual newlines
        // duck.ai's syntax highlighter creates by laying out each line as its own block.
        if (tag === 'pre') {
            const code = node.querySelector('code');
            let lang = '';
            if (code) {
                const m = (code.className || '').match(/language-([\w+-]+)/);
                if (m) lang = m[1];
            }
            let text = (code || node).innerText;
            text = text.replace(/^\n+|\n+$/g, '');
            return '\n```' + lang + '\n' + text + '\n```\n\n';
        }

        const children = Array.from(node.childNodes).map(nodeToMd).join('');

        switch (tag) {
            case 'p':       return children + '\n\n';
            case 'br':      return '\n';
            case 'h1':      return '\n# '   + children + '\n\n';
            case 'h2':      return '\n## '  + children + '\n\n';
            case 'h3':      return '\n### ' + children + '\n\n';
            case 'h4':
            case 'h5':
            case 'h6':      return '\n#### ' + children + '\n\n';
            case 'strong':
            case 'b':       return '**' + children + '**';
            case 'em':
            case 'i':       return '*' + children + '*';
            case 'code':    return '`' + children + '`';
            case 'ul':
            case 'ol':      return children + '\n';
            case 'li':      return '- ' + children.trim() + '\n';
            case 'a': {
                const href = node.getAttribute('href');
                return href ? '[' + children + '](' + href + ')' : children;
            }
            case 'hr':      return '\n---\n\n';
            case 'blockquote': return children.split('\n').map(l => '> ' + l).join('\n') + '\n\n';
            default:        return children;
        }
    }

    let md = nodeToMd(el);

    // Drop orphan language labels duck.ai renders adjacent to code blocks:
    //   "python\n```python\n..."  →  "```python\n..."
    md = md.replace(
        new RegExp('([a-z+#-]{1,15})\\s*\\n+```([a-z+#-]*)', 'gi'),
        (m, label, fenceLang) => {
            if (KNOWN_LANGS.has(label.toLowerCase())) return '```' + (fenceLang || label);
            return m;
        }
    );

    md = md.replace(/\n{3,}/g, '\n\n').trim();
    return md;
}
"""


def _extract_answer(page) -> str:
    """Return the last assistant message rendered as markdown, with citations stripped."""
    bodies = page.locator("[data-activeresponse]")
    n = bodies.count()
    if n:
        return bodies.nth(n - 1).evaluate(_TO_MARKDOWN_JS)
    return ""


def _dump_debug(page, label: str) -> Path:
    DEBUG_DIR.mkdir(exist_ok=True)
    path = DEBUG_DIR / f"{label}.html"
    path.write_text(page.content(), encoding="utf-8")
    return path


class DuckSession:
    def __init__(self, headless: bool = True, browser_name: str = "firefox", use_profile: bool = False):
        self._pw = sync_playwright().start()
        self._browser_name = browser_name
        self._use_profile = use_profile
        self._turn = 0

        self._open_browser(headless=headless)

        textarea_ok = self._wait_for_input(timeout=15_000)
        if not textarea_ok or self._is_captcha_present():
            if headless and use_profile:
                self._solve_captcha_visibly()
            elif not textarea_ok:
                self.close()
                raise RuntimeError("Could not find duck.ai's input.")

    def _open_browser(self, headless: bool):
        browser_type = getattr(self._pw, self._browser_name)
        if self._use_profile:
            _ensure_profile_dir()
            self._ctx = browser_type.launch_persistent_context(str(PROFILE_DIR), headless=headless)
            self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self._closer = self._ctx
        else:
            self._browser = browser_type.launch(headless=headless)
            self.page = self._browser.new_page()
            self._closer = self._browser

        self.page.goto(URL, wait_until="domcontentloaded")
        _click_if_present(self.page, "I agree", "Accept", "Continue", "Got it", "Next")
        _click_if_present(self.page, "I agree", "Accept", "Continue", "Got it", "Next")

    def _is_captcha_present(self) -> bool:
        return self.page.locator(
            "[data-type='modal-overlay'], [data-testid*='anomaly-modal']"
        ).count() > 0

    def _wait_for_input(self, timeout: int = 15_000) -> bool:
        try:
            self.page.locator("textarea").first.wait_for(state="visible", timeout=timeout)
            return True
        except PlaywrightTimeout:
            return False

    def _solve_captcha_visibly(self):
        print("[chatnik] CAPTCHA detected — closing headless, opening visible browser...")
        self._closer.close()
        _release_profile_locks()

        browser_type = getattr(self._pw, self._browser_name)
        ctx = browser_type.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded")

        print("[chatnik] Solve the CAPTCHA in the browser window (up to 5 min)...")
        try:
            page.locator("textarea").first.wait_for(state="visible", timeout=300_000)
            print("[chatnik] CAPTCHA solved! Closing visible browser...")
            time.sleep(1)  # let Firefox flush cookies to disk
        except PlaywrightTimeout:
            ctx.close()
            raise RuntimeError("CAPTCHA not solved within 5 minutes.")
        finally:
            ctx.close()

        _release_profile_locks()
        print("[chatnik] Reopening headless browser...")
        self._open_browser(headless=True)

        if not self._wait_for_input(timeout=15_000):
            self.close()
            raise RuntimeError("Could not find duck.ai's input after CAPTCHA solve.")

        print("[chatnik] Headless browser ready.")

    def _wait_for_response(self, before_count: int, timeout_ms: int = ANSWER_WAIT_MS) -> bool:
        deadline = time.time() + timeout_ms / 1000
        appeared = False
        last_text: str | None = None
        stable_ticks = 0

        while time.time() < deadline:
            bodies = self.page.locator("[data-activeresponse]")
            n = bodies.count()

            if not appeared:
                if n > before_count:
                    appeared = True
            else:
                # aria-busy means duck.ai is still generating
                if self.page.locator("[aria-busy='true']").count() > 0:
                    stable_ticks = 0
                    last_text = None
                else:
                    try:
                        text = bodies.nth(n - 1).inner_text()
                        if text and "Generating" not in text:
                            if text == last_text:
                                stable_ticks += 1
                                if stable_ticks >= 2:
                                    self.page.wait_for_timeout(300)
                                    return True
                            else:
                                stable_ticks = 0
                                last_text = text
                        else:
                            stable_ticks = 0
                    except Exception:
                        pass

            time.sleep(0.4)

        return appeared

    def ask(self, prompt: str, debug: bool = False) -> str:
        self._turn += 1
        textarea = self.page.locator("textarea").first
        try:
            textarea.wait_for(state="visible", timeout=8_000)
        except PlaywrightTimeout:
            if self._use_profile:
                self._solve_captcha_visibly()
            else:
                raise RuntimeError("textarea disappeared mid-session")
        else:
            if self._is_captcha_present() and self._use_profile:
                self._solve_captcha_visibly()

        before = self.page.locator("[data-activeresponse]").count()
        textarea.fill(prompt)
        textarea.press("Enter")

        if not self._wait_for_response(before):
            self.page.wait_for_timeout(ANSWER_WAIT_MS)

        answer = _extract_answer(self.page)

        if debug or not answer:
            dump = _dump_debug(self.page, f"turn{self._turn:02d}")
            if not answer:
                answer = f"[no selector matched — DOM saved to {dump}]"
            else:
                print(f"(debug DOM saved → {dump})")
        return answer

    def close(self):
        try:
            self._closer.close()
        finally:
            self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
