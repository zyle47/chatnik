import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

URL = "https://duck.ai/"
ANSWER_WAIT_MS = 12_000
DEBUG_DIR = Path(__file__).parent / "debug"

PROFILE_DIR = Path(__file__).parent / "pw_profile"


def _profile_dir() -> str:
    PROFILE_DIR.mkdir(exist_ok=True)
    return str(PROFILE_DIR)



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
    bodies = page.locator(".space-y-4.whitespace-normal") # bodies = page.locator("[data-activeresponse]") # 
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
    def __init__(self, headless: bool = True, browser_name: str = "firefox"):
        self._pw = sync_playwright().start()
        browser_type = getattr(self._pw, browser_name)
        use_profile = not headless or PROFILE_DIR.exists()
        if use_profile:
            self._ctx = browser_type.launch_persistent_context(_profile_dir(), headless=headless)
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

        # Wait for the response to finish streaming (text stabilizes for 2 ticks)
        deadline = time.time() + ANSWER_WAIT_MS / 1000
        last = ""
        stable = 0
        while time.time() < deadline:
            time.sleep(0.4)
            cur = _extract_answer(self.page)
            if cur and cur != "Generating response" and cur == last:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
                last = cur

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
