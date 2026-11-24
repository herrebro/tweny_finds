"""Base de datos SQLite: artículos de los canales, búsqueda de texto (FTS5) y embeddings de fotos."""
import json
import sqlite3
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    msg_id INTEGER NOT NULL,
    date TEXT,
    text TEXT,
    links TEXT,   -- lista JSON de enlaces
    photos TEXT,  -- lista JSON de rutas relativas a DATA_DIR
    UNIQUE(channel, msg_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    text, tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS embeddings(
    item_id INTEGER NOT NULL,
    photo_idx INTEGER NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY(item_id, photo_idx)
);
CREATE TABLE IF NOT EXISTS state(
    channel TEXT PRIMARY KEY,
    last_id INTEGER NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Abre una conexión nueva. Cada hilo debe abrir la suya."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


def last_msg_id(conn, channel: str) -> int:
    row = conn.execute("SELECT last_id FROM state WHERE channel=?", (channel,)).fetchone()
    return row["last_id"] if row else 0


def set_last_msg_id(conn, channel: str, msg_id: int) -> None:
    conn.execute(
        "INSERT INTO state(channel, last_id) VALUES(?, ?) "
        "ON CONFLICT(channel) DO UPDATE SET last_id=excluded.last_id",
        (channel, msg_id),
    )
    conn.commit()


def upsert_item(conn, channel, msg_id, date, text, links, photos) -> int:
    text = text or ""
    row = conn.execute(
        "SELECT id FROM items WHERE channel=? AND msg_id=?", (channel, msg_id)
    ).fetchone()
    values = (date, text, json.dumps(links), json.dumps(photos))
    if row:
        item_id = row["id"]
        conn.execute("UPDATE items SET date=?, text=?, links=?, photos=? WHERE id=?", (*values, item_id))
        conn.execute("DELETE FROM items_fts WHERE rowid=?", (item_id,))
    else:
        cur = conn.execute(
            "INSERT INTO items(channel, msg_id, date, text, links, photos) VALUES(?,?,?,?,?,?)",
            (channel, msg_id, *values),
        )
        item_id = cur.lastrowid
    conn.execute("INSERT INTO items_fts(rowid, text) VALUES(?, ?)", (item_id, text))
    conn.commit()
    return item_id


def items_without_embeddings(conn):
    """Devuelve (item_id, [(photo_idx, ruta_relativa), ...]) de los artículos con fotos sin embedding."""
    out = []
    for row in conn.execute("SELECT id, photos FROM items").fetchall():
        photos = json.loads(row["photos"] or "[]")
        done = {
            r["photo_idx"]
            for r in conn.execute("SELECT photo_idx FROM embeddings WHERE item_id=?", (row["id"],))
        }
        pending = [(i, p) for i, p in enumerate(photos) if i not in done]
        if pending:
            out.append((row["id"], pending))
    return out


def save_embedding(conn, item_id: int, photo_idx: int, vec: np.ndarray) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO embeddings(item_id, photo_idx, vec) VALUES(?,?,?)",
        (item_id, photo_idx, np.asarray(vec, dtype=np.float32).tobytes()),
    )


def load_matrix(conn):
    """Carga todos los embeddings: (ids de artículo, matriz [n, dim])."""
    rows = conn.execute("SELECT item_id, vec FROM embeddings").fetchall()
    if not rows:
        return np.array([], dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
    ids = np.array([r["item_id"] for r in rows], dtype=np.int64)
    mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    return ids, mat


def stats(conn) -> dict:
    items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    photos = sum(len(json.loads(r["photos"] or "[]")) for r in conn.execute("SELECT photos FROM items"))
    embedded = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    return {"items": items, "photos": photos, "embedded": embedded}
