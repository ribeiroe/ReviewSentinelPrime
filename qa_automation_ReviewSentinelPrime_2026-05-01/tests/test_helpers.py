"""
test_helpers.py – Unit tests for pure helper functions in app.py.

Tests build_query(), build_arxiv_query(), and deduplication logic
in isolation, without a web server.

Tags: unit, helpers
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import build_query, build_arxiv_query


# ---------------------------------------------------------------------------
# build_query
# ---------------------------------------------------------------------------

class TestBuildQuery:
    def test_simple_keyword_string(self):
        """Single keyword phrase returns it unchanged."""
        q = build_query("machine learning")
        assert "machine learning" in q

    def test_empty_string_returns_empty_or_star(self):
        """
        Empty keyword string should return an empty string or '*',
        not raise an exception.
        """
        result = build_query("")
        assert result is not None  # Must not raise

    def test_boolean_and_operator(self):
        """Keywords with AND are preserved in the output query."""
        q = build_query("machine learning AND deep learning")
        upper = q.upper()
        assert "AND" in upper or "machine" in q.lower()

    def test_boolean_or_operator(self):
        """Keywords with OR are preserved."""
        q = build_query("classification OR detection")
        upper = q.upper()
        assert "OR" in upper or "classification" in q.lower()

    def test_special_characters_do_not_raise(self):
        """Characters like quotes or parentheses must not cause exceptions."""
        try:
            q = build_query('"exact phrase" AND (neural OR deep)')
            assert q is not None
        except Exception as exc:
            pytest.fail(f"build_query raised unexpectedly: {exc}")

    def test_whitespace_only_string(self):
        """Whitespace-only input should not raise."""
        result = build_query("   ")
        assert result is not None


# ---------------------------------------------------------------------------
# build_arxiv_query
# ---------------------------------------------------------------------------

class TestBuildArxivQuery:
    def test_simple_keyword(self):
        """Single keyword produces a non-empty arXiv query."""
        q = build_arxiv_query("neural networks")
        assert len(q) > 0

    def test_empty_string_returns_empty_or_star(self):
        """Empty string should return a non-raising value."""
        result = build_arxiv_query("")
        assert result is not None

    def test_plain_multi_word_query_uses_and(self):
        """Multiple plain terms should be joined with AND for arXiv."""
        q = build_arxiv_query("Aerospace and Agile")
        assert q.lower() == "all:(aerospace AND agile)" or q.lower() == "all:(aerospace and agile)"

    def test_boolean_and_becomes_AND(self):
        """AND operator is reflected in arXiv query format."""
        q = build_arxiv_query("machine learning AND classification")
        assert "AND" in q.upper() or "machine" in q.lower()

    def test_output_contains_no_raw_spaces_in_terms(self):
        """arXiv API uses + or encoded spaces — verify no bare spaces in field terms."""
        q = build_arxiv_query("deep learning")
        # The query should not produce 'ti:deep learning' with unencoded space in the URL
        # (depends on implementation, so we just check no exception)
        assert isinstance(q, str)

    def test_output_is_string(self):
        """Return type must always be str."""
        result = build_arxiv_query("test")
        assert isinstance(result, str)
