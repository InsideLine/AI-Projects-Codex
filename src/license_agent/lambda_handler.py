from __future__ import annotations

try:
    from mangum import Mangum
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install the aws extra to run the Lambda handler.") from exc

from .api import app


handler = Mangum(app)
