# bugs/BUG_TEMPLATE.md – Bug Report Template

## Bug ID
BUG-{number} – {short title}

## Severity
[ ] Critical | [ ] High | [ ] Medium | [ ] Low

## Status
[ ] Open | [ ] In Progress | [ ] Fixed | [ ] Verified Fixed | [ ] Closed

---

## Summary
_One-sentence description of the defect._

## Environment
- OS: Windows 11 / macOS / Ubuntu
- Python: 3.x.x
- Flask: 3.1.0
- Branch / Commit: `abc1234`
- Test file: `tests/test_XYZ.py`
- Test function: `test_function_name`

## Steps to Reproduce
1. Start the application: `python app.py`
2. ...
3. ...

## Expected Behavior
_What should happen._

## Actual Behavior
_What actually happens (include error messages / stack traces)._

## Root Cause Analysis
_Which line(s) in which file(s) cause the bug._

## Fix Applied
_Description of the code change made to resolve the issue._

**File(s) changed:**
- `path/to/file.py` lines XX–YY

## Fix Verification
_Test(s) re-run after fix. Paste the passing test output here._

```
PASSED tests/test_XYZ.py::test_function_name
```

## Notes
_Any additional context, related issues, or follow-up tasks._
