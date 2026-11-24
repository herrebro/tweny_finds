"""Embeddings de fotos con CLIP en versión ONNX (fastembed): ligero, sin PyTorch.
El modelo (unos 350 MB) se descarga la primera vez y se guarda en DATA_DIR/models."""
import numpy as np

import config
import db

_model = None
BATCH = 16


def _get_model():
    global _model
    if _model is None:
        from fastembed import ImageEmbedding

        config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        _model = ImageEmbedding(model_name=config.IMAGE_MODEL, cache_dir=str(config.MODELS_DIR))
    return _model


def embed_images(paths) -> np.ndarray:
    vecs = list(_get_model().embed([str(p) for p in paths], batch_size=BATCH))
    arr = np.vstack(vecs).astype(np.float32)
    return arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)


def embed_image(path) -> np.ndarray:
    return embed_images([path])[0]


def run(conn) -> int:
    """Calcula los embeddings que faltan. Devuelve cuántas fotos se han procesado."""
    todo = []
    for item_id, photos in db.items_without_embeddings(conn):
        for idx, rel in photos:
            path = config.DATA_DIR / rel
            if path.exists():
                todo.append((item_id, idx, path))
    count = 0
    for start in range(0, len(todo), BATCH):
        chunk = todo[start : start + BATCH]
        try:
            vecs = embed_images([p for _, _, p in chunk])
            pairs = [(it, ix, v) for (it, ix, _), v in zip(chunk, vecs)]
        except Exception:  # alguna foto rota: se reintenta una a una
            pairs = []
            for it, ix, p in chunk:
                try:
                    pairs.append((it, ix, embed_image(p)))
                except Exception as exc:
                    print(f"aviso: no se pudo procesar {p.name}: {exc}", flush=True)
        for it, ix, v in pairs:
            db.save_embedding(conn, it, ix, v)
            count += 1
        conn.commit()
    return count
