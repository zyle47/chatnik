import argparse
import sys
from duck_browser import DuckSession


def main():
    parser = argparse.ArgumentParser(description="Ask duck.ai via headless browser.")
    parser.add_argument("prompt", nargs="*", help="Initial prompt (skip to enter REPL)")
    parser.add_argument("--show", action="store_true", help="Run browser visibly (non-headless)")
    parser.add_argument("--browser", default="firefox", choices=["firefox", "chromium", "webkit"])
    parser.add_argument("--profile", action="store_true", help="Use a copy of your real Firefox profile")
    parser.add_argument("--debug", action="store_true", help="Dump page HTML each turn to ./debug/")
    parser.add_argument("--once", action="store_true", help="Exit after the first answer (no REPL)")
    args = parser.parse_args()

    initial = " ".join(args.prompt).strip()

    mode = "visible" if args.show else "headless"
    print(f"Opening duck.ai ({args.browser}, {mode})...")

    try:
        with DuckSession(headless=not args.show, browser_name=args.browser, use_profile=args.profile) as sess:
            print("Ready.\n")

            if initial:
                print(f"You: {initial}")
                print(f"\nDuck:\n{sess.ask(initial, debug=args.debug)}\n")
                if args.once:
                    return

            while True:
                try:
                    q = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not q:
                    continue
                if q.lower() in ("quit", "exit", ":q"):
                    break
                print(f"\nDuck:\n{sess.ask(q, debug=args.debug)}\n")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
