"""
SQLite-backed storage providing Redis-compatible async operations.
Drop-in replacement for aioredis usage across telefire plugins.
"""
import json
import random
import time
import aiosqlite
from pathlib import Path

DEFAULT_DB = str(Path.home() / ".telefire" / "data.db")


class Storage:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB
        self._conn = None

    async def connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sets (
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                UNIQUE(name, value)
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS hashes (
                name TEXT NOT NULL,
                key TEXT NOT NULL,
                value INTEGER DEFAULT 0,
                UNIQUE(name, key)
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS streams (
                name TEXT NOT NULL,
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                data TEXT NOT NULL
            )
        """)
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_streams_name ON streams(name, id DESC)"
        )
        await self._conn.commit()
        return self

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # --- Set operations ---

    async def sadd(self, name, *values):
        for v in values:
            await self._conn.execute(
                "INSERT OR IGNORE INTO sets (name, value) VALUES (?, ?)",
                (name, str(v))
            )
        await self._conn.commit()

    async def smembers(self, name):
        cursor = await self._conn.execute(
            "SELECT value FROM sets WHERE name = ?", (name,)
        )
        rows = await cursor.fetchall()
        return {r[0] for r in rows}

    async def sismember(self, name, value):
        cursor = await self._conn.execute(
            "SELECT 1 FROM sets WHERE name = ? AND value = ? LIMIT 1",
            (name, str(value))
        )
        return await cursor.fetchone() is not None

    async def srandmember(self, name, count=1):
        cursor = await self._conn.execute(
            "SELECT value FROM sets WHERE name = ?", (name,)
        )
        rows = await cursor.fetchall()
        values = [r[0] for r in rows]
        if not values:
            return []
        return random.sample(values, min(count, len(values)))

    async def isscan(self, name):
        """Async iterator over set members (replaces Redis SSCAN)."""
        cursor = await self._conn.execute(
            "SELECT value FROM sets WHERE name = ?", (name,)
        )
        async for row in cursor:
            yield row[0]

    # --- Hash operations ---

    async def hincrby(self, name, key, amount=1):
        await self._conn.execute(
            "INSERT INTO hashes (name, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(name, key) DO UPDATE SET value = value + ?",
            (name, str(key), amount, amount)
        )
        await self._conn.commit()
        cursor = await self._conn.execute(
            "SELECT value FROM hashes WHERE name = ? AND key = ?",
            (name, str(key))
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def hset(self, name, mapping=None, **kwargs):
        data = mapping or kwargs
        for k, v in data.items():
            await self._conn.execute(
                "INSERT INTO hashes (name, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(name, key) DO UPDATE SET value = ?",
                (name, str(k), v, v)
            )
        await self._conn.commit()

    async def hgetall(self, name):
        cursor = await self._conn.execute(
            "SELECT key, value FROM hashes WHERE name = ?", (name,)
        )
        rows = await cursor.fetchall()
        return {r[0]: str(r[1]) for r in rows}

    async def delete(self, *names):
        for name in names:
            await self._conn.execute("DELETE FROM sets WHERE name = ?", (name,))
            await self._conn.execute("DELETE FROM hashes WHERE name = ?", (name,))
            await self._conn.execute("DELETE FROM streams WHERE name = ?", (name,))
        await self._conn.commit()

    # --- Stream operations ---

    async def xadd(self, name, fields):
        data = json.dumps(fields)
        ts = time.time()
        cursor = await self._conn.execute(
            "INSERT INTO streams (name, timestamp, data) VALUES (?, ?, ?)",
            (name, ts, data)
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def xrange(self, name, count=100):
        cursor = await self._conn.execute(
            "SELECT id, data FROM streams WHERE name = ? ORDER BY id ASC LIMIT ?",
            (name, count)
        )
        rows = await cursor.fetchall()
        return [(str(r[0]), json.loads(r[1])) for r in rows]

    async def xrevrange(self, name, count=100):
        cursor = await self._conn.execute(
            "SELECT id, data FROM streams WHERE name = ? ORDER BY id DESC LIMIT ?",
            (name, count)
        )
        rows = await cursor.fetchall()
        return [(str(r[0]), json.loads(r[1])) for r in rows]

    # --- Pipeline (simple transaction wrapper) ---

    def pipeline(self, transaction=True):
        return _Pipeline(self)


class _Pipeline:
    """Minimal pipeline that batches operations in a transaction."""
    def __init__(self, storage):
        self._storage = storage

    async def __aenter__(self):
        return self._storage

    async def __aexit__(self, *args):
        await self._storage._conn.commit()
