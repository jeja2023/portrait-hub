from __future__ import annotations

import copy
from pathlib import Path

from tools.portrait_support_matrix import load_matrix, matrix_status, render_markdown, validate_matrix

ROOT = Path(__file__).resolve().parents[1]


def test_support_matrix_is_structurally_valid_and_versioned() -> None:
    matrix = load_matrix()
    status = matrix_status(matrix)

    assert status["ok"] is True
    assert status["product_version"] == "0.18.0"
    assert len(status["sha256"]) == 64
    assert validate_matrix(matrix) == []


def test_commercial_profiles_remain_blocked_without_real_evidence() -> None:
    matrix = load_matrix()

    standard = matrix_status(matrix, target_profile="private_standard")
    development = matrix_status(matrix, target_profile="development")

    assert standard["ok"] is True
    assert standard["commercial_ready"] is False
    assert standard["support_level"] == "limited"
    assert standard["blockers"]
    assert development["commercial_ready"] is False


def test_support_matrix_rejects_unqualified_sla_claim() -> None:
    matrix = copy.deepcopy(load_matrix())
    standard = next(item for item in matrix["profiles"] if item["id"] == "private_standard")
    standard["commercial_sla"] = True

    errors = validate_matrix(matrix)

    assert any("cannot enable commercial_sla" in error for error in errors)


def test_human_support_matrix_includes_machine_digest_and_blockers() -> None:
    matrix = load_matrix()
    markdown = render_markdown(matrix)
    digest = matrix_status(matrix)["sha256"]

    assert digest in markdown
    assert "private_standard" in markdown
    assert "Five commercial model artifacts" in markdown
    assert "0.17.0 -> 0.18.0" in markdown
    assert (ROOT / "docs" / "deployment" / "SUPPORT_MATRIX.md").read_text(encoding="utf-8") == markdown
