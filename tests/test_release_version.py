from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _version_from(pattern: str, path: str) -> str:
    match = re.search(pattern, _read(path))
    assert match is not None, f"version not found in {path}"
    return match.group(1)


def test_release_version_is_synchronized() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        expected = str(tomllib.load(file)["project"]["version"])

    root_package = json.loads(_read("package.json"))
    console_package = json.loads(_read("frontend/console-next/package.json"))
    package_lock = json.loads(_read("package-lock.json"))
    pom = ET.fromstring(_read("sdk/java/pom.xml"))
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    pom_version = pom.findtext("m:version", namespaces=namespace)

    versions = {
        "package.json": root_package["version"],
        "frontend/console-next/package.json": console_package["version"],
        "package-lock.json": package_lock["version"],
        "package-lock root package": package_lock["packages"][""]["version"],
        "package-lock console package": package_lock["packages"]["frontend/console-next"]["version"],
        "app/settings.py": _version_from(r'APP_VERSION = "([^"]+)"', "app/settings.py"),
        "Python SDK": _version_from(r'SDK_VERSION = "([^"]+)"', "sdk/python/portrait_hub_client.py"),
        "Node SDK": _version_from(r'const SDK_VERSION = "([^"]+)"', "sdk/node/portraitHubClient.js"),
        "Go SDK": _version_from(r'const SDKVersion = "([^"]+)"', "sdk/go/portraithub/client.go"),
        "Java SDK": _version_from(
            r'public static final String SDK_VERSION = "([^"]+)"',
            "sdk/java/src/main/java/com/portraithub/sdk/PortraitHubClient.java",
        ),
        "Java Maven artifact": pom_version,
    }
    assert versions == {name: expected for name in versions}

    readme = _read("README.md")
    changelog = _read("更新日志.md")
    assert f"当前版本：{expected}。" in readme
    assert re.search(rf"^## \[{re.escape(expected)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE)
    assert (ROOT / "docs" / "releases" / f"{expected}.md").is_file()
