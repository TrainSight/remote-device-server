from __future__ import annotations

import hashlib
import re
from typing import Optional

from fastapi import Depends, HTTPException, Header

from server.config import settings

# Only allow safe characters in usernames to prevent path traversal
_USERNAME_RE = re.compile(r'^[a-zA-Z0-9._-]{1,64}$')


def _key_to_fallback_username(api_key: str) -> str:
    """Derive a stable username from the API key (legacy / no-username clients)."""
    return "user_" + hashlib.sha256(api_key.encode()).hexdigest()[:8]


async def verify_api_key(
    x_api_key: str = Header(...),
    x_rds_username: Optional[str] = Header(None),
) -> str:
    """Validate the API key and return the effective username.

    Priority:
      1. X-RDS-Username header (set by modern rds clients)
      2. Fallback: short hash of the API key (old clients / scripts)
    """
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if x_rds_username and _USERNAME_RE.match(x_rds_username):
        return x_rds_username

    # Legacy fallback — keeps old clients working without workspace isolation
    return _key_to_fallback_username(x_api_key)
