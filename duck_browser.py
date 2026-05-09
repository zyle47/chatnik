import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

URL = "https://duck.ai/"
ANSWER_WAIT_MS = 12_000
DEBUG_DIR = Path(__file__).parent / "debug"

DEFAULT_FIREFOX_PROFILE = Path.home() / "snap/firefox/common/.mozilla/firefox/itb0s38f.default-1760976178135"
PROFILE_COPY = Path(__file__).parent / "fox_profile"

_PROFILE_IGNORE = shutil.ignore_patterns(
    "*.lock", "lock", "parent.lock", ".parentlock",
    "cache2", "startupCache", "Cache*",
    "thumbnails", "safebrowsing", "crashes",
    "OfflineCache", "shader-cache",
)


def _ensure_profile_copy() -> str:
    if PROFILE_COPY.exists():
        return str(PROFILE_COPY)
    if not DEFAULT_FIREFOX_PROFILE.exists():
        raise RuntimeError(f"Firefox profile not found at {DEFAULT_FIREFOX_PROFILE}")
    print(f"Copying Firefox profile (one-time) → {PROFILE_COPY}")
    shutil.copytree(DEFAULT_FIREFOX_PROFILE, PROFILE_COPY, ignore=_PROFILE_IGNORE)
    for name in ("lock", "parent.lock", ".parentlock"):
        (PROFILE_COPY / name).unlink(missing_ok=True)
    return str(PROFILE_COPY)


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
    const clone = el.cloneNode(true);
    // Strip citation widgets
    clone.querySelectorAll('button, a[aria-label]').forEach(n => n.remove());

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

        // pre = code block; handle specially so we don't lose newlines
        if (tag === 'pre') {
            const code = node.querySelector('code');
            let lang = '';
            if (code) {
                const m = (code.className || '').match(/language-([\w+-]+)/);
                if (m) lang = m[1];
            }
            // textContent on a pre preserves whitespace including newlines
            let text = (code || node).textContent;
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

    let md = nodeToMd(clone);

    // Drop orphan language labels that duck.ai renders next to code blocks:
    //   "python\n```python\n..."  →  "```python\n..."
    md = md.replace(
        new RegExp('([a-z+#-]{1,15})\\s*\\n+```([a-z+#-]*)', 'gi'),
        (m, label, fenceLang) => {
            if (KNOWN_LANGS.has(label.toLowerCase())) return '```' + (fenceLang || label);
            return m;
        }
    );

    // Collapse 3+ newlines to 2
    md = md.replace(/\n{3,}/g, '\n\n').trim();
    return md;
}
"""


def _extract_answer(page) -> str:
    """Return the last assistant message rendered as markdown, with citations stripped."""
    bodies = page.locator(".space-y-4.whitespace-normal")
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
        browser_type = getattr(self._pw, browser_name)
        if use_profile:
            user_data_dir = _ensure_profile_copy()
            self._ctx = browser_type.launch_persistent_context(user_data_dir, headless=headless)
            self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self._closer = self._ctx
        else:
            self._browser = browser_type.launch(headless=headless)
            self.page = self._browser.new_page()
            self._closer = self._browser

        self.page.goto(URL, wait_until="domcontentloaded")
        _click_if_present(self.page, "I agree", "Accept", "Continue", "Got it", "Next")
        _click_if_present(self.page, "I agree", "Accept", "Continue", "Got it", "Next")

        try:
            self.page.locator("textarea").first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeout:
            self.close()
            raise RuntimeError("Could not find duck.ai's input.")

        self._turn = 0

    def ask(self, prompt: str, debug: bool = False) -> str:
        self._turn += 1
        textarea = self.page.locator("textarea").first
        textarea.fill(prompt)
        textarea.press("Enter")
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
