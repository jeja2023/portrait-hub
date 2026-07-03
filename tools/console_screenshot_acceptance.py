from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class ConsoleAcceptanceError(RuntimeError):
    pass


REQUIRED_MARKERS = [
    "console-app",
    "/assets/console/api/client.js",
    "/assets/console/state/store.js",
    "/assets/console/views/app.js",
]

CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def _console_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/console"


def _find_chrome_executable() -> Path | None:
    configured = os.getenv("PLAYWRIGHT_CHROME_EXECUTABLE") or os.getenv("PORTRAIT_CHROME_EXECUTABLE")
    if configured:
        configured_path = Path(configured)
        if configured_path.exists():
            return configured_path
    for candidate in CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    for command in ("chrome.exe", "chrome", "msedge.exe", "msedge"):
        found = shutil.which(command)
        if found:
            return Path(found)
    return None


def _missing_markers(html: str) -> list[str]:
    return [item for item in REQUIRED_MARKERS if item not in html]


def _summarize_stderr(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return "\n".join(lines[-8:])


def _chrome_profile_root() -> Path:
    root = Path(os.getenv("PORTRAIT_CHROME_PROFILE_ROOT", ".codex-tmp"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_playwright_acceptance(*, url: str, output: Path, timeout_ms: int) -> dict[str, Any]:
    output = output.resolve()
    from playwright.sync_api import sync_playwright

    output.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        chrome = _find_chrome_executable()
        try:
            browser = playwright.chromium.launch()
        except Exception:
            if chrome is None:
                raise
            browser = playwright.chromium.launch(executable_path=str(chrome))
        page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_selector("#console-app", timeout=timeout_ms)
        body_text = page.locator("body").inner_text(timeout=timeout_ms)
        html = page.content()
        missing = _missing_markers(html)
        if missing:
            browser.close()
            raise ConsoleAcceptanceError("控制台 DOM 缺少必要标记: " + ", ".join(missing))
        page.screenshot(path=str(output), full_page=True)
        status = {
            "ok": True,
            "renderer": "playwright",
            "url": url,
            "screenshot": str(output),
            "body_text_length": len(body_text),
        }
        browser.close()
        return status


def _run_chrome_acceptance(*, url: str, output: Path, timeout_ms: int) -> dict[str, Any]:
    output = output.resolve()
    chrome = _find_chrome_executable()
    if chrome is None:
        raise ConsoleAcceptanceError("未找到本机 Chrome 或 Edge，无法执行截图验收")

    output.parent.mkdir(parents=True, exist_ok=True)
    timeout_seconds = max(30, int(timeout_ms / 1000) + 10)
    profile = tempfile.mkdtemp(prefix="portrait-console-chrome-", dir=_chrome_profile_root())
    try:
        base_args = [
            str(chrome),
            "--headless=new",
            "--disable-background-networking",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            f"--user-data-dir={profile}",
            "--window-size=1440,960",
            f"--virtual-time-budget={timeout_ms}",
        ]
        dom = subprocess.run(
            [*base_args, "--dump-dom", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if dom.returncode != 0:
            raise ConsoleAcceptanceError("本机 Chrome DOM 验收失败: " + _summarize_stderr(dom.stderr))
        html = dom.stdout
        missing = _missing_markers(html)
        if missing:
            raise ConsoleAcceptanceError("控制台 DOM 缺少必要标记: " + ", ".join(missing))

        shot = subprocess.run(
            [*base_args, f"--screenshot={output}", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if shot.returncode != 0:
            raise ConsoleAcceptanceError("本机 Chrome 截图失败: " + _summarize_stderr(shot.stderr))
        if not output.exists() or output.stat().st_size <= 0:
            raise ConsoleAcceptanceError("本机 Chrome 未生成有效截图文件")
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    body_text = re.sub(r"<[^>]+>", "", html)
    return {
        "ok": True,
        "renderer": "chrome-headless",
        "url": url,
        "screenshot": str(output),
        "body_text_length": len(body_text),
    }


def run_console_acceptance(*, base_url: str, output: Path, timeout_ms: int) -> dict[str, Any]:
    url = _console_url(base_url)
    try:
        return _run_playwright_acceptance(url=url, output=output, timeout_ms=timeout_ms)
    except ImportError:
        return _run_chrome_acceptance(url=url, output=output, timeout_ms=timeout_ms)
    except Exception as exc:
        try:
            return _run_chrome_acceptance(url=url, output=output, timeout_ms=timeout_ms)
        except ConsoleAcceptanceError as chrome_exc:
            raise ConsoleAcceptanceError(f"截图验收失败: {chrome_exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="捕获并校验 PortraitHub 控制台截图。")
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
        print(f"控制台截图验收: {'OK' if report['ok'] else 'FAILED'}")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
