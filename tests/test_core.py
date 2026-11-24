"""Pruebas sin red: base de datos, búsquedas y lectura de la vista pública (con una página de ejemplo)."""
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import db  # noqa: E402
import scrape  # noqa: E402
import search  # noqa: E402

SAMPLE_HTML = """
<html><body>
<div class="tgme_header_info"><a class="tgme_page_photo_image" style="background-image:url('https://cdn/avatar.jpg')"></a></div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="canalx/101">
  <div class="tgme_widget_message_bubble">
   <div class="tgme_widget_message_reply"><a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn/reply.jpg')"></a></div>
   <div class="tgme_widget_message_grouped_wrap">
     <a class="tgme_widget_message_photo_wrap" href="https://t.me/canalx/101" style="width:300px;background-image:url('https://cdn/a1.jpg')"></a>
     <a class="tgme_widget_message_photo_wrap" href="https://t.me/canalx/101" style="width:300px;background-image:url('https://cdn/a2.jpg')"></a>
   </div>
   <div class="tgme_widget_message_text js-message_text" dir="auto">Nike Tech Fleece <a href="?q=%23nike">#nike</a><br/>Weidian: <a href="https://weidian.com/item.html?itemID=123">link</a><br/>Usfans https://www.usfans.com/product/1?id=9.</div>
   <div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="https://t.me/canalx/101"><time datetime="2026-11-20T10:00:00+00:00" class="time">10:00</time></a></div>
  </div>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message js-widget_message" data-post="canalx/100">
  <div class="tgme_widget_message_bubble">
   <div class="tgme_widget_message_text js-message_text" dir="auto">Solo texto sin enlaces</div>
   <div class="tgme_widget_message_footer"><a class="tgme_widget_message_date"><time datetime="2026-11-19T09:00:00+00:00">09:00</time></a></div>
  </div>
 </div>
</div>
</body></html>
"""


def make_conn():
    return db.connect(Path(tempfile.mkdtemp()) / "t.db")


def test_parse_page():
    msgs = scrape.parse_page(SAMPLE_HTML)
    assert [m["msg_id"] for m in msgs] == [101, 100]
    m = msgs[0]
    assert m["channel"] == "canalx"
    assert m["photo_urls"] == ["https://cdn/a1.jpg", "https://cdn/a2.jpg"]  # sin avatar ni miniatura de respuesta
    assert "Nike Tech Fleece" in m["text"] and "\nWeidian:" in m["text"]
    assert m["links"] == [
        "https://weidian.com/item.html?itemID=123",
        "https://www.usfans.com/product/1?id=9",
    ]
    assert m["date"].startswith("2026-11-20")
    assert msgs[1]["links"] == [] and msgs[1]["photo_urls"] == []


def test_text_search_and_upsert():
    conn = make_conn()
    a = db.upsert_item(conn, "canal", 1, "2026-01-01", "Nike Tech Fleece hoodie negro", ["https://x/1"], ["photos/a.jpg"])
    db.upsert_item(conn, "canal", 2, "2026-01-02", "Stone Island chaqueta", ["https://x/2"], [])
    r = search.search_text(conn, "nike hoodie")
    assert [x["msg_id"] for x in r] == [1]
    assert r[0]["links"] == ["https://x/1"]
    assert search.search_text(conn, "chaq")[0]["msg_id"] == 2
    assert {x["msg_id"] for x in search.search_text(conn, "nike chaqueta")} == {1, 2}
    assert db.upsert_item(conn, "canal", 1, "2026-01-01", "Nike Tech Fleece pantalón", [], []) == a
    assert search.search_text(conn, "hoodie") == []
    assert len(search.search_text(conn, "pantalón")) == 1
    assert search.search_text(conn, "¿¿??") == []


def test_image_search_pending_and_cache():
    conn = make_conn()
    i1 = db.upsert_item(conn, "canal", 1, "d", "uno", [], ["photos/a.jpg", "photos/b.jpg"])
    i2 = db.upsert_item(conn, "canal", 2, "d", "dos", [], ["photos/c.jpg"])
    assert sum(len(p) for _, p in db.items_without_embeddings(conn)) == 3
    db.save_embedding(conn, i1, 0, np.array([1, 0, 0], dtype=np.float32))
    db.save_embedding(conn, i1, 1, np.array([0, 1, 0], dtype=np.float32))
    conn.commit()
    assert search.search_image(conn, np.array([0, 0, 1], dtype=np.float32))[0]["msg_id"] == 1
    db.save_embedding(conn, i2, 0, np.array([0, 0, 1], dtype=np.float32))  # la caché debe renovarse
    conn.commit()
    assert db.items_without_embeddings(conn) == []
    res = search.search_image(conn, np.array([0, 0.9, 0.1], dtype=np.float32), limit=2)
    assert res[0]["msg_id"] == 1 and res[0]["score"] > 0.9
    assert len(res) == 2 and res[1]["msg_id"] == 2
    assert db.stats(conn) == {"items": 2, "photos": 3, "embedded": 3}


def test_state_caption_and_channels():
    conn = make_conn()
    assert db.last_msg_id(conn, "c") == 0
    db.set_last_msg_id(conn, "c", 10)
    db.set_last_msg_id(conn, "c", 12)
    assert db.last_msg_id(conn, "c") == 12
    item = {"channel": "canal", "msg_id": 5, "text": "hola", "links": ["https://a"], "photos": [], "score": 0.8}
    cap = search.caption(item, with_score=True)
    assert "https://t.me/canal/5" in cap and "https://a" in cap and "0.80" in cap
    assert config.normalize_channel("https://t.me/Universalfind") == "Universalfind"
    assert config.normalize_channel("@bladefindss") == "bladefindss"
    assert config.normalize_channel("https://t.me/s/cnbuysheets?before=3") == "cnbuysheets"


def test_threads_use_own_connection():
    from concurrent.futures import ThreadPoolExecutor

    path = Path(tempfile.mkdtemp()) / "t.db"
    conn = db.connect(path)
    db.upsert_item(conn, "c", 1, "d", "hola mundo", [], [])

    def work():
        c = db.connect(path)
        try:
            return len(search.search_text(c, "hola"))
        finally:
            c.close()

    with ThreadPoolExecutor(2) as ex:
        assert list(ex.map(lambda _: work(), range(2))) == [1, 1]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK: todas las pruebas pasan")
