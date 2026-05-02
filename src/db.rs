use anyhow::Result;
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::cell::RefCell;
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_ENTRIES: i64 = 50;

#[derive(Clone, Debug, Serialize)]
pub struct Entry {
    pub id: i64,
    pub kind: String,
    pub content: Option<String>,
    pub image_path: Option<String>,
    pub pinned: bool,
    pub created_at: f64,
}

pub struct Database {
    conn: RefCell<Connection>,
    #[allow(dead_code)]
    images_dir: PathBuf,
}

impl Database {
    pub fn open() -> Result<Self> {
        let data_dir = crate::data_dir();
        let images_dir = data_dir.join("images");
        fs::create_dir_all(&images_dir)?;

        let conn = Connection::open(data_dir.join("history.db"))?;
        conn.execute_batch(
            "
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
            ",
        )?;

        Ok(Self {
            conn: RefCell::new(conn),
            images_dir,
        })
    }

    #[allow(dead_code)]
    pub fn images_dir(&self) -> &PathBuf {
        &self.images_dir
    }

    pub fn add_text(&self, text: &str) -> Result<Option<i64>> {
        let trimmed = text.trim();
        if trimmed.is_empty() {
            return Ok(None);
        }

        let hash = hash_bytes(text.as_bytes());
        self.add_entry("text", Some(text), None, &hash)
    }

    #[allow(dead_code)]
    pub fn add_image(&self, image_path: &str, bytes: &[u8]) -> Result<Option<i64>> {
        let hash = hash_bytes(bytes);
        self.add_entry("image", None, Some(image_path), &hash)
    }

    fn add_entry(
        &self,
        kind: &str,
        content: Option<&str>,
        image_path: Option<&str>,
        hash: &str,
    ) -> Result<Option<i64>> {
        let conn = self.conn.borrow();
        let existing: Option<i64> = conn
            .query_row(
                "SELECT id FROM entries WHERE hash = ?1",
                params![hash],
                |row| row.get(0),
            )
            .optional()?;

        let now = now_seconds();
        if let Some(id) = existing {
            conn.execute(
                "UPDATE entries SET created_at = ?1 WHERE hash = ?2",
                params![now, hash],
            )?;
            return Ok(Some(id));
        }

        conn.execute(
            "INSERT INTO entries (type, content, image_path, hash, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![kind, content, image_path, hash, now],
        )?;
        let id = conn.last_insert_rowid();
        drop(conn);
        self.prune()?;
        Ok(Some(id))
    }

    pub fn entries(&self, limit: i64) -> Result<Vec<Entry>> {
        let conn = self.conn.borrow();
        let mut stmt = conn.prepare(
            "SELECT id, type, content, image_path, pinned, created_at
             FROM entries
             ORDER BY pinned DESC, created_at DESC
             LIMIT ?1",
        )?;

        let rows = stmt.query_map(params![limit], |row| {
            Ok(Entry {
                id: row.get(0)?,
                kind: row.get(1)?,
                content: row.get(2)?,
                image_path: row.get(3)?,
                pinned: row.get::<_, i64>(4)? != 0,
                created_at: row.get(5)?,
            })
        })?;

        rows.collect::<rusqlite::Result<Vec<_>>>().map_err(Into::into)
    }

    pub fn entry(&self, id: i64) -> Result<Option<Entry>> {
        self.conn
            .borrow()
            .query_row(
                "SELECT id, type, content, image_path, pinned, created_at
                 FROM entries
                 WHERE id = ?1",
                params![id],
                |row| {
                    Ok(Entry {
                        id: row.get(0)?,
                        kind: row.get(1)?,
                        content: row.get(2)?,
                        image_path: row.get(3)?,
                        pinned: row.get::<_, i64>(4)? != 0,
                        created_at: row.get(5)?,
                    })
                },
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn toggle_pin(&self, id: i64) -> Result<()> {
        self.conn.borrow().execute(
            "UPDATE entries
             SET pinned = CASE pinned WHEN 0 THEN 1 ELSE 0 END
             WHERE id = ?1",
            params![id],
        )?;
        Ok(())
    }

    pub fn delete_entry(&self, id: i64) -> Result<()> {
        let image_path: Option<String> = self
            .conn
            .borrow()
            .query_row(
                "SELECT image_path FROM entries WHERE id = ?1",
                params![id],
                |row| row.get(0),
            )
            .optional()?
            .flatten();

        if let Some(path) = image_path {
            let _ = fs::remove_file(path);
        }

        self.conn
            .borrow()
            .execute("DELETE FROM entries WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub fn clear_unpinned(&self) -> Result<()> {
        let image_paths = {
            let conn = self.conn.borrow();
            let mut stmt = conn.prepare("SELECT image_path FROM entries WHERE pinned = 0")?;
            let rows = stmt
                .query_map([], |row| row.get::<_, Option<String>>(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            rows
        };

        for path in image_paths.into_iter().flatten() {
            let _ = fs::remove_file(path);
        }

        self.conn
            .borrow()
            .execute("DELETE FROM entries WHERE pinned = 0", [])?;
        Ok(())
    }

    fn prune(&self) -> Result<()> {
        let conn = self.conn.borrow();
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM entries WHERE pinned = 0",
            [],
            |row| row.get(0),
        )?;

        if count <= MAX_ENTRIES {
            return Ok(());
        }

        let mut stmt = conn.prepare(
            "SELECT id, image_path FROM entries
             WHERE pinned = 0
             ORDER BY created_at ASC
             LIMIT ?1",
        )?;
        let stale = stmt
            .query_map(params![count - MAX_ENTRIES], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, Option<String>>(1)?))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;

        for (id, image_path) in stale {
            if let Some(path) = image_path {
                let _ = fs::remove_file(path);
            }
            conn.execute("DELETE FROM entries WHERE id = ?1", params![id])?;
        }

        Ok(())
    }
}

pub fn hash_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
