"""Infrastructure adapters.

This is the only package allowed to import cloud provider SDKs
(``google.cloud.*``, ``google.genai.*``). Business modules depend on small
protocols and receive adapters through the composition root (``cli.py``).
See ADR-001 and ADR-006 in ``docs/decisions.md``.
"""

from __future__ import annotations

__all__: list[str] = []
