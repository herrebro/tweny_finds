"""Lee la vista pública de los canales (https://t.me/s/<canal>): no hace falta cuenta de Telegram.

Solo funciona con canales públicos que tengan la vista previa web activada.
"""
import logging
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from PIL import Image

import config
import db

log = logging.getLogger("scrape")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en,es;q=0.8",
}
BG_RE = re.compile(r"background-image:\s*url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")
PAUSE = 1.0  # segundos entre peticiones, para no cargar el servicio

# Estado de la última lectura de cada canal, para el comando /estado
STATUS: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")


def fetch_page(channel: str, before: int | None = None) -> str:
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    last_exc = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"no se pudo leer {url}: {last_exc}")


def _links(text_el, text: str) -> list[str]:
    found: list[str] = []
    if text_el is not None:
        for a in text_el.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("http"):
                found.append(href)
    found += URL_RE.findall(text)
    seen, out = set(), []
    for link in found:
        link = link.strip().rstrip(".,;")
        # se descartan los enlaces internos de hashtags de Telegram
        if re.match(r"https?://t\.me/s/[^?]+\?q=", link):
            continue
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def parse_page(html: str) -> list[dict]:
    """Extrae las publicaciones de una página de la vista pública."""
    soup = BeautifulSoup(html, "html.parser")
    messages = []
    for el in soup.select("div.tgme_widget_message[data-post]"):
        channel, _, mid = el["data-post"].partition("/")
        if not mid.isdigit():
            continue
        text_el = el.select_one(".tgme_widget_message_text")
        text = ""
        if text_el is not None:
            for br in text_el.find_all("br"):
                br.replace_with("\n")
            text = text_el.get_text().strip()
        photos = []
        for a in el.select("a.tgme_widget_message_photo_wrap"):
            if a.find_parent(class_="tgme_widget_message_reply"):
                continue  # miniatura de una respuesta, no es foto del mensaje
            m = BG_RE.search(a.get("style", ""))
            if m:
                photos.append(m.group(1))
        time_el = el.select_one("time[datetime]")
        messages.append(
            {
                "channel": channel,
                "msg_id": int(mid),
                "date": time_el["datetime"] if time_el else None,
                "text": text,
                "links": _links(text_el, text),
                "photo_urls": photos,
            }
        )
    return messages


def _download(url: str, dest) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        with Image.open(dest) as im:
            im = im.convert("RGB")
            im.thumbnail((512, 512))
            im.save(dest, "JPEG", quality=82)
        return True
    except Exception as exc:
        log.warning("foto no descargada (%s): %s", url[:80], exc)
        dest.unlink(missing_ok=True)
        return False


def scrape_channel(conn, channel: str, max_pages: int) -> int:
    """Trae las publicaciones nuevas de un canal. Devuelve cuántas guardó."""
    state_key = channel.lower()
    last = db.last_msg_id(conn, state_key)
    fresh: list[dict] = []
    before = None
    seen_any = False
    for _ in range(max_pages):
        msgs = parse_page(fetch_page(channel, before))
        if not msgs:
            break
        seen_any = True
        msgs.sort(key=lambda m: m["msg_id"], reverse=True)
        fresh += [m for m in msgs if m["msg_id"] > last]
        oldest = msgs[-1]["msg_id"]
        if oldest <= last or oldest <= 1:
            break
        before = oldest
        time.sleep(PAUSE)
    if not seen_any:
        raise RuntimeError("sin publicaciones: ¿el canal es público y tiene vista previa web?")

    stored = 0
    for m in sorted(fresh, key=lambda m: m["msg_id"]):
        photos = []
        for idx, url in enumerate(m["photo_urls"]):
            rel = f"photos/{m['channel']}_{m['msg_id']}_{idx}.jpg"
            dest = config.DATA_DIR / rel
            if dest.exists() or _download(url, dest):
                photos.append(rel)
        if not (m["text"] or m["links"] or photos):
            continue
        db.upsert_item(conn, m["channel"], m["msg_id"], m["date"], m["text"], m["links"], photos)
        stored += 1
    if fresh:
        db.set_last_msg_id(conn, state_key, max(m["msg_id"] for m in fresh))
    return stored


def run_once() -> None:
    """Lee todos los canales y calcula los embeddings de las fotos nuevas."""
    conn = db.connect(config.DB_PATH)
    try:
        for channel in config.channels():
            first_time = db.last_msg_id(conn, channel.lower()) == 0
            pages = config.FIRST_RUN_PAGES if first_time else config.UPDATE_PAGES
            try:
                n = scrape_channel(conn, channel, pages)
                STATUS[channel] = f"ok, {n} nuevos ({_now()})"
                log.info("%s: %s artículos nuevos", channel, n)
            except Exception as exc:
                STATUS[channel] = f"ERROR: {exc} ({_now()})"
                log.error("%s: %s", channel, exc)
            time.sleep(PAUSE)
        try:
            import embed

            done = embed.run(conn)
            STATUS["fotos"] = f"{done} fotos procesadas ({_now()})"
            log.info("embeddings: %s fotos procesadas", done)
        except Exception as exc:
            STATUS["fotos"] = f"ERROR: {exc} ({_now()})"
            log.exception("fallo calculando embeddings")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_once()
