"""Tests for the sandboxed arithmetic and data-analysis tools."""

from __future__ import annotations

from llm_service.tools import analyze_structured_data, safe_calculate


def test_safe_calculate_evaluates_arithmetic() -> None:
    """Bounded arithmetic should evaluate to the expected result."""
    assert safe_calculate("(12 + 3) * 4 / 2") == "30.0"


def test_safe_calculate_rejects_code_execution() -> None:
    """Anything beyond numeric arithmetic must be refused, not executed."""
    assert "only numeric arithmetic is allowed" in safe_calculate("__import__('os').getcwd()")


def test_safe_calculate_rejects_oversized_power() -> None:
    """Huge exponents must be rejected before they can exhaust memory."""
    assert "outside the safe limit" in safe_calculate("10 ** 50")


def test_analyze_structured_data_counts_json_rows() -> None:
    """A JSON array should be counted without executing any code."""
    assert analyze_structured_data('[{"track":"main","papers":2},{"track":"main","papers":3}]') == "2"


def test_analyze_structured_data_sums_csv_field() -> None:
    """CSV aggregation should sum a numeric column."""
    assert analyze_structured_data("track,papers\nmain,2\nworkshop,3\n", operation="sum", field="papers") == "5.0"


def test_analyze_structured_data_reads_nested_unique_values() -> None:
    """Dotted field paths should reach nested values for unique extraction."""
    result = analyze_structured_data(
        '[{"author":{"name":"Ada"}},{"author":{"name":"Grace"}}]',
        operation="unique",
        field="author.name",
    )
    assert result == '["Ada", "Grace"]'


def test_analyze_structured_data_reports_missing_field() -> None:
    """A missing field should produce a clear error, not a crash."""
    assert "missing" in analyze_structured_data('[{"a":1}]', operation="sum", field="b")
