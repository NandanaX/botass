"""Single-scanner guarantee.

With PostgreSQL a session-level advisory lock is held on a dedicated connection for the
lifetime of the process; only the holder runs the scanner. Other API instances serve
requests only and retry periodically, taking over if the leader dies (its connection
closes and the lock is released). SQLite is single-host by nature, so the process is
always the leader there.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

log = logging.getLogger(__name__)

SCANNER_LOCK_KEY = 0x435259505453  # "CRYPTS"


class LeaderLock:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._conn: AsyncConnection | None = None
        self.is_leader = False

    async def try_acquire(self) -> bool:
        if self.is_leader:
            return True
        if self.engine.dialect.name != "postgresql":
            self.is_leader = True
            return True
        conn = await self.engine.connect()
        try:
            got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SCANNER_LOCK_KEY})).scalar()
        except Exception:
            await conn.close()
            raise
        if got:
            self._conn = conn
            self.is_leader = True
            log.info("acquired scanner leadership")
        else:
            await conn.close()
        return bool(got)

    async def release(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SCANNER_LOCK_KEY})
            finally:
                await self._conn.close()
                self._conn = None
        self.is_leader = False
