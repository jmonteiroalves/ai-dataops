"""Allow ``python -m ai_dataops`` to behave like the ``dataops`` command."""

from __future__ import annotations

from ai_dataops.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
