from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np


def jsonable(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays and datetimes into JSON-safe values.
    Non-finite floats become None."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, datetime):
        return value.isoformat()
    return value
