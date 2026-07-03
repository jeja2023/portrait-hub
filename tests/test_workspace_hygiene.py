from pathlib import Path

import tools.workspace_hygiene as workspace_hygiene
from tools.workspace_hygiene import artifact_size, clean_artifacts, iter_cache_artifacts


def test_workspace_hygiene_finds_cache_artifacts(workspace_tmp_path) -> None:
    pycache = workspace_tmp_path / "pkg" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "module.pyc").write_bytes(b"cache")
    keep = workspace_tmp_path / "pkg" / "module.py"
    keep.write_text("print('keep')\n", encoding="utf-8")

    artifacts = sorted(iter_cache_artifacts(workspace_tmp_path))

    assert pycache.resolve() in artifacts
    assert keep.resolve() not in artifacts
    assert artifact_size(pycache) >= len(b"cache")


def test_workspace_hygiene_cleans_cache_entries(monkeypatch, workspace_tmp_path) -> None:
    cache_dir = workspace_tmp_path / ".pytest_cache"
    cache_dir.mkdir()
    cache_file = workspace_tmp_path / "module.pyc"
    cache_file.write_bytes(b"cache")

    calls: list[tuple[str, Path]] = []
    original_rmtree = workspace_hygiene.shutil.rmtree

    def fake_rmtree(path, *args, **kwargs) -> None:
        if kwargs.get("ignore_errors"):
            return original_rmtree(path, *args, **kwargs)
        calls.append(("rmtree", Path(path).resolve()))

    def fake_unlink(self, missing_ok: bool = False) -> None:  # noqa: ARG001
        calls.append(("unlink", self.resolve()))

    monkeypatch.setattr("tools.workspace_hygiene.shutil.rmtree", fake_rmtree)
    monkeypatch.setattr(Path, "unlink", fake_unlink, raising=False)

    clean_artifacts([cache_dir.resolve(), cache_file.resolve()])

    assert ("rmtree", cache_dir.resolve()) in calls
    assert ("unlink", cache_file.resolve()) in calls


def test_workspace_hygiene_skips_heavy_runtime_directories(workspace_tmp_path) -> None:
    skipped_pycache = workspace_tmp_path / ".venv" / "pkg" / "__pycache__"
    skipped_pycache.mkdir(parents=True)
    (skipped_pycache / "module.pyc").write_bytes(b"cache")

    artifacts = list(iter_cache_artifacts(workspace_tmp_path))

    assert skipped_pycache.resolve() not in artifacts

def test_workspace_hygiene_reports_site_packages_residuals(workspace_tmp_path) -> None:
    residual = workspace_tmp_path / ".venv" / "Lib" / "site-packages" / "~json-stubs"
    residual.mkdir(parents=True)
    (residual / "__init__.pyi").write_text("", encoding="utf-8")
    skipped_pycache = workspace_tmp_path / ".venv" / "Lib" / "site-packages" / "pkg" / "__pycache__"
    skipped_pycache.mkdir(parents=True)
    (skipped_pycache / "module.pyc").write_bytes(b"cache")

    artifacts = workspace_hygiene.safe_cache_artifacts(workspace_tmp_path)

    assert residual.resolve() in {path.resolve() for path in artifacts}
    assert skipped_pycache.resolve() not in {path.resolve() for path in artifacts}
