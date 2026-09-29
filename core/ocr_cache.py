"""Bounded local OCR cache; never stores credentials or image bytes."""
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path


class OCRCache:
    def __init__(self, path: Path, limit: int = 10000):
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS readings (cache_key TEXT PRIMARY KEY, text TEXT NOT NULL, saved REAL NOT NULL)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS readings_saved ON readings(saved)")
            connection.commit()

    @staticmethod
    def _key(key: tuple) -> str:
        return hashlib.sha256(json.dumps(key, ensure_ascii=True, default=str).encode()).hexdigest()

    def get_many(self, keys: list[tuple]) -> dict[tuple, str]:
        hashed = {self._key(key): key for key in keys}
        result = {}
        try:
            with self._lock, closing(sqlite3.connect(self.path, timeout=1)) as connection:
                ids = list(hashed)
                for offset in range(0, len(ids), 400):
                    batch = ids[offset:offset + 400]
                    placeholders = ",".join("?" for _ in batch)
                    for digest, text in connection.execute(
                        f"SELECT cache_key, text FROM readings WHERE cache_key IN ({placeholders})", batch
                    ):
                        result[hashed[digest]] = text
        except (sqlite3.Error, OSError):
            pass  # A cache failure must not prevent editing.
        return result

    def put(self, key: tuple, text: str) -> None:
        try:
            with self._lock, closing(sqlite3.connect(self.path, timeout=1)) as connection:
                connection.execute("INSERT OR REPLACE INTO readings VALUES (?, ?, ?)",
                                   (self._key(key), text, time.time()))
                connection.execute(
                    "DELETE FROM readings WHERE cache_key IN "
                    "(SELECT cache_key FROM readings ORDER BY saved DESC LIMIT -1 OFFSET ?)",
                    (self.limit,),
                )
                connection.commit()
        except (sqlite3.Error, OSError):
            pass
