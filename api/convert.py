"""POST /api/convert en Vercel. La sesión se comprueba en qlik2pbi.http_api."""

from __future__ import annotations

import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from qlik2pbi.http_api import handler as _Handler  # noqa: E402


class handler(_Handler):
    pass
