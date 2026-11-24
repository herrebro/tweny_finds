"""Búsqueda por texto (FTS5) y por imagen (similitud de embeddings) sobre la base de datos."""
import json
import re

import numpy as np

import db

_cache = {"key": None, "ids": None, "mat": None}


def _row_to_dict(row, score=None) -> dict:
    d = dict(row)
    d["links"] = json.loads(d.get("links") or "[]")
    d["photos"] = json.loads(d.get("photos") or "[]")
    d["score"] = score
    return d


def search_text(conn, query: str, limit: int = 5) -> list[dict]:
    tokens = re.findall(r"\w+", query.lower())
    if not tokens:
        return []
    # Primero todas las palabras (AND); si no hay nada, cualquiera de ellas (OR).
    for joiner in (" ", " OR "):
        match = joiner.join(f'"{t}"*' for t in tokens)
        rows = conn.execute(
            "SELECT items.*, bm25(items_fts) AS score FROM items_fts "
            "JOIN items ON items.id = items_fts.rowid "
            "WHERE items_fts MATCH ? ORDER BY score LIMIT ?",
            (match, limit),
        ).fetchall()
        if rows:
            return [_row_to_dict(r, r["score"]) for r in rows]
    return []


def _matrix(conn):
    """Matriz de embeddings normalizada, guardada en memoria hasta que cambie la tabla."""
    dbfile = conn.execute("PRAGMA database_list").fetchone()["file"]
    row = conn.execute("SELECT COUNT(*) AS c, COALESCE(MAX(rowid), 0) AS m FROM embeddings").fetchone()
    key = (dbfile, row["c"], row["m"])
    if _cache["key"] != key:
        ids, mat = db.load_matrix(conn)
        if len(ids):
            mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        _cache.update(key=key, ids=ids, mat=mat)
    return _cache["ids"], _cache["mat"]


def search_image(conn, query_vec: np.ndarray, limit: int = 5, min_score: float = 0.0) -> list[dict]:
    ids, mat = _matrix(conn)
    if len(ids) == 0:
        return []
    q = np.asarray(query_vec, dtype=np.float32)
    q = q / (np.linalg.norm(q) + 1e-9)
    scores = mat @ q
    best: dict[int, float] = {}
    for item_id, s in zip(ids.tolist(), scores.tolist()):
        if s > best.get(item_id, -1.0):
            best[item_id] = s
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    results = []
    for item_id, s in ranked:
        if s < min_score:
            continue
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        results.append(_row_to_dict(row, s))
    return results


def source_link(item: dict) -> str:
    return f"https://t.me/{item['channel']}/{item['msg_id']}"


def caption(item: dict, with_score: bool = False) -> str:
    text = (item["text"] or "").strip()
    text = re.sub(r"\n{2,}", "\n", text)
    if len(text) > 350:
        text = text[:350].rstrip() + "…"
    parts = [text] if text else []
    if item["links"]:
        parts.append("Enlaces:\n" + "\n".join(item["links"][:6]))
    parts.append("Fuente: " + source_link(item))
    if with_score and item["score"] is not None:
        parts.append(f"Parecido: {item['score']:.2f}")
    return "\n\n".join(parts)[:1000]
