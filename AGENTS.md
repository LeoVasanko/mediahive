# MediaHive agent notes

## Python 3.14 syntax: unparenthesized `except` is valid

This project targets Python **>= 3.14** (see `requires-python` in
`pyproject.toml`). Per [PEP 758](https://peps.python.org/pep-0758/) (Final,
Python 3.14), multiple exception types may be caught **without parentheses**:

```python
except OSError, ValueError:   # valid Python 3.14+, equivalent to except (OSError, ValueError):
```

Parentheses are still required when an `as` clause is used:
`except (OSError, ValueError) as e:`.

Do not "fix" these into tuple form, and do not flag them as Python 2 remnant
syntax errors — that rule is obsolete training data. Any syntax validation,
compilation check, or linting of this codebase must run under Python 3.14+
(e.g. `python -m py_compile` with a 3.14 interpreter); older interpreters
will report false SyntaxErrors on this and other 3.14-only constructs.
