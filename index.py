"""Vercel entrypoint.

Vercel looks for a top-level ASGI or WSGI callable named ``app``.
The local server in ``scripts/serve_local.py`` is unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from server import wsgi_app  # noqa: E402

app = wsgi_app
