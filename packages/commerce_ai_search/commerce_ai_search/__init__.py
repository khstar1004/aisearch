"""Commerce AI product search service.

This package is the productized home for the current Haeorum reference
implementation. During the migration window, legacy operational scripts still
import ``app.*`` and ``scripts.*``. The aliases below keep those imports working
when the service is started from ``commerce_ai_search`` entrypoints.
"""

from __future__ import annotations

import sys
from pathlib import Path


PACKAGE_PARENT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = PACKAGE_PARENT / "legacy"

if LEGACY_ROOT.exists():
    legacy_path = str(LEGACY_ROOT)
    if legacy_path not in sys.path:
        sys.path.insert(0, legacy_path)

sys.modules.setdefault("app", sys.modules[__name__])

