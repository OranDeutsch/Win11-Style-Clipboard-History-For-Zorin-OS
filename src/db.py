"""
SQLite database management for clipboard history.

Handles schema initialization, entry insertion with duplicate detection,
and automated pruning of old entries to keep the database size manageable.
"""
import sqlite3
import hashlib
import os
import time
from pathlib import Path

DATA_DIR = Path.home() / '.local' / 'share' / 'clipboard-history'
DB_PATH = DATA_DIR / 'history.db'
IMAGES_DIR = DATA_DIR / 'images'
MAX_ENTRIES = 50  # Maximum number of unpinned entries to retain


class Database:
    """Encapsulates all SQLite operations for the clipboard daemon."""

    def __init__(self):
        """Initialize the database and ensure the schema is applied."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        """Create tables and indexes if they do not exist."""
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS entries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                type       TEXT NOT NULL,
                content    TEXT,
                image_path TEXT,
                hash       TEXT NOT NULL,
                pinned     INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_hash ON entries(hash);
            CREATE INDEX IF NOT EXISTS idx_time ON entries(created_at DESC);
        ''')
        self.conn.commit()

    def add_entry(self, type_: str, content: str = None,
                  image_path: str = None, raw: bytes = None) -> int | None:
        """Add a new clipboard entry or update the timestamp of an existing one."""
        # Calculate hash for duplicate detection
        if raw is not None:
            hash_ = hashlib.sha256(raw).hexdigest()
        elif content:
            hash_ = hashlib.sha256(content.encode()).hexdigest()
        else:
            return None

        # Duplicate detection: If content exists, just bump the timestamp 
        # to bring it to the top of the history list.
        row = self.conn.execute(
            'SELECT id FROM entries WHERE hash = ?', (hash_,)
        ).fetchone()
        if row:
            self.conn.execute(
                'UPDATE entries SET created_at = ? WHERE hash = ?',
                (time.time(), hash_)
            )
            self.conn.commit()
            return row[0]

        # Insert new entry
        cursor = self.conn.execute(
            'INSERT INTO entries (type, content, image_path, hash, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (type_, content, image_path, hash_, time.time())
        )
        self.conn.commit()
        self._prune()  # Cleanup old entries
        return cursor.lastrowid

    def _prune(self):
        """Remove old unpinned entries exceeding the MAX_ENTRIES limit."""
        count = self.conn.execute(
            'SELECT COUNT(*) FROM entries WHERE pinned = 0'
        ).fetchone()[0]
        if count > MAX_ENTRIES:
            rows = self.conn.execute(
                'SELECT id, image_path FROM entries WHERE pinned = 0 '
                'ORDER BY created_at ASC LIMIT ?',
                (count - MAX_ENTRIES,)
            ).fetchall()
            for id_, img in rows:
                if img and os.path.exists(img):
                    try:
                        os.remove(img)
                    except OSError:
                        pass
                self.conn.execute('DELETE FROM entries WHERE id = ?', (id_,))
        self.conn.commit()

    def get_entries(self, limit: int = 100):
        """Retrieve the most recent entries, prioritizing pinned ones."""
        return self.conn.execute(
            'SELECT id, type, content, image_path, pinned, created_at '
            'FROM entries ORDER BY pinned DESC, created_at DESC LIMIT ?',
            (limit,)
        ).fetchall()

    def toggle_pin(self, id_: int):
        """Flip the pinned state of an entry."""
        pinned = self.conn.execute(
            'SELECT pinned FROM entries WHERE id = ?', (id_,)
        ).fetchone()
        if pinned:
            new = 0 if pinned[0] else 1
            self.conn.execute(
                'UPDATE entries SET pinned = ? WHERE id = ?', (new, id_)
            )
            self.conn.commit()

    def delete_entry(self, id_: int):
        """Delete a specific entry and its associated image file."""
        row = self.conn.execute(
            'SELECT image_path FROM entries WHERE id = ?', (id_,)
        ).fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try:
                os.remove(row[0])
            except OSError:
                pass
        self.conn.execute('DELETE FROM entries WHERE id = ?', (id_,))
        self.conn.commit()

    def clear_unpinned(self):
        """Remove all unpinned entries from the database."""
        rows = self.conn.execute(
            'SELECT image_path FROM entries WHERE pinned = 0'
        ).fetchall()
        for (img,) in rows:
            if img and os.path.exists(img):
                try:
                    os.remove(img)
                except OSError:
                    pass
        self.conn.execute('DELETE FROM entries WHERE pinned = 0')
        self.conn.commit()

    def get_latest_hash(self) -> str | None:
        """Return the hash of the most recently added entry."""
        row = self.conn.execute(
            'SELECT hash FROM entries ORDER BY created_at DESC LIMIT 1'
        ).fetchone()
        return row[0] if row else None

    def close(self):
        """Close the database connection."""
        self.conn.close()
