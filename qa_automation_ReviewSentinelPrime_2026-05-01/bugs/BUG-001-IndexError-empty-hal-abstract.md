# BUG-001 – IndexError on empty HAL abstract_s list

## Bug ID
BUG-001 – IndexError on empty HAL `abstract_s` list

## Severity
[x] High

## Status
[x] Fixed | [x] Verified Fixed

---

## Summary
`search_hal()` in `app.py` raises `IndexError: list index out of range` when a HAL
document has an empty list `[]` for the `abstract_s` (or `title_s`) field.

## Environment
- OS: Windows 11 10.0.26100
- Python: 3.12.0
- Flask: 3.1.0
- File: `app.py` line 632
- Test file: `tests/test_search_endpoint.py`
- Test functions:
  - `test_year_filter_removes_out_of_range_docs`
  - `test_exclude_venues_filter`

## Steps to Reproduce
1. Mock the HAL API to return a document where `abstract_s: []`
2. Call `POST /search` with `databases: ["hal"]`
3. Observe `IndexError: list index out of range`

## Root Cause Analysis
```python
# BUGGY CODE — app.py line 632
"abstract": doc.get("abstract_s", [""])[0] if isinstance(doc.get("abstract_s"), list) else doc.get("abstract_s", ""),
```

`dict.get(key, default)` only returns the default when the **key is absent**.
When the key IS present but its value is `[]` (empty list), `.get()` returns `[]` and
`[][0]` raises `IndexError`.

The same pattern on `title_s` (line 628) has the same latent bug.

## Fix Applied
Replace the unsafe index access with an `or`-guarded expression that treats an
empty list identically to a missing key:

```python
# FIXED CODE
"abstract": (doc.get("abstract_s") or [""])[0] if isinstance((doc.get("abstract_s") or [""]), list) else doc.get("abstract_s", ""),
```

Simplified (cleaner version actually applied):
```python
abstract_raw = doc.get("abstract_s", "")
"abstract": abstract_raw[0] if isinstance(abstract_raw, list) and abstract_raw else (abstract_raw if isinstance(abstract_raw, str) else ""),
```

**File changed:** `app.py` lines 628–632

## Fix Verification
```
PASSED tests/test_search_endpoint.py::test_year_filter_removes_out_of_range_docs
PASSED tests/test_search_endpoint.py::test_exclude_venues_filter
=================== 130 passed, 0 failed in 2.4s ===================
```

## Notes
The same pattern appears on `title_s`. Both were fixed simultaneously.
