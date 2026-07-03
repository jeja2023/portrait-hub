from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class ConsoleAcceptanceError(RuntimeError):
    pass


def run_console_acceptance(*, base_url: str, output: Path, timeout_ms: int) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - 可选浏览器依赖
        raise ConsoleAcceptanceError("playwright is not installed; install it and run `python -m playwright install chromium`") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
        page.goto(base_url.rstrip("/") + "/console", wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_selector("#console-app", timeout=timeout_ms)
        body_text = page.locator("body").inner_text(timeout=timeout_ms)
        required = ["console-app", "/assets/console/api/client.js", "/assets/console/state/store.js", "/assets/console/views/app.js"]
        html = page.content()
        missing = [item for item in required if item not in html]
        if missing:
            browser.close()
            raise ConsoleAcceptanceError("console DOM missing required markers: " + ", ".join(missing))
        page.screenshot(path=str(output), full_page=True)
        status = {
            "ok": True,
            "url": base_url.rstrip("/") + "/console",
            "screenshot": str(output),
            "body_text_length": len(body_text),
        }
        browser.close()
        return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture and validate the PortraitHub console with Playwright.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="artifacts/console-acceptance.png")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = run_console_acceptance(base_url=args.base_url, output=Path(args.output), timeout_ms=args.timeout_ms)
    except ConsoleAcceptanceError as exc:
        report = {"ok": False, "error": str(exc)}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"console screenshot acceptance: {'OK' if report['ok'] else 'FAILED'}")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

