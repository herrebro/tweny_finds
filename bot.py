"""Bot de Telegram: escribe un nombre o manda una foto y devuelve artículos de los canales.
Un solo proceso: el bot contesta y, en segundo plano, vuelve a leer los canales cada cierto tiempo.
"""
import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import config
import db
import scrape
import search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tweny-bot")

MAX_RESULTS = 5


def _with_conn(fn, *args):
    """Cada hilo abre su propia conexión a la base de datos."""
    conn = db.connect(config.DB_PATH)
    try:
        return fn(conn, *args)
    finally:
        conn.close()


async def authorized(update: Update) -> bool:
    allowed = config.allowed_users()
    user = update.effective_user
    if not allowed:
        await update.message.reply_text(
            f"Tu id de Telegram es {user.id}. Ponlo en la variable ALLOWED_USERS y reinicia el bot."
        )
        return False
    if user.id not in allowed:
        log.warning("Usuario no autorizado: %s", user.id)
        return False
    return True


async def send_results(update: Update, results: list[dict], with_score: bool) -> None:
    if not results:
        await update.message.reply_text("No he encontrado nada. Prueba con otra palabra o con otra foto.")
        return
    for item in results:
        text = search.caption(item, with_score=with_score)
        photo_path = config.DATA_DIR / item["photos"][0] if item["photos"] else None
        if photo_path and photo_path.exists():
            with open(photo_path, "rb") as fh:
                await update.message.reply_photo(fh, caption=text)
        else:
            await update.message.reply_text(text, disable_web_page_preview=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update):
        return
    await update.message.reply_text(
        "Escríbeme un nombre o una marca, o mándame una foto, y te devuelvo artículos de tus canales.\n"
        "/estado te dice si todo está funcionando."
    )


def _status_text(conn) -> str:
    s = db.stats(conn)
    lines = [
        f"Artículos guardados: {s['items']}",
        f"Fotos: {s['photos']} (listas para buscar por foto: {s['embedded']})",
        "",
    ]
    for name, st in scrape.STATUS.items():
        lines.append(f"{name}: {st}")
    if not scrape.STATUS:
        lines.append("Todavía no ha terminado la primera lectura de canales; espera unos minutos.")
    return "\n".join(lines)


async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update):
        return
    await update.message.reply_text(await asyncio.to_thread(_with_conn, _status_text))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update):
        return
    results = await asyncio.to_thread(_with_conn, search.search_text, update.message.text, MAX_RESULTS)
    await send_results(update, results, with_score=False)


def _image_search(conn, path: str) -> list[dict]:
    import embed  # el modelo solo se carga cuando hace falta

    return search.search_image(conn, embed.embed_image(path), MAX_RESULTS)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update):
        return
    tg_file = await update.message.photo[-1].get_file()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "query.jpg"
        await tg_file.download_to_drive(str(path))
        try:
            results = await asyncio.to_thread(_with_conn, _image_search, str(path))
        except Exception:
            log.exception("fallo en la búsqueda por foto")
            await update.message.reply_text("No he podido procesar la foto ahora mismo. Mira /estado.")
            return
    await send_results(update, results, with_score=True)


async def ingest_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(scrape.run_once)
        except Exception:
            log.exception("fallo en la lectura de canales")
        await asyncio.sleep(config.SCRAPE_INTERVAL_MIN * 60)


async def post_init(app: Application) -> None:
    app.bot_data["ingest_task"] = asyncio.create_task(ingest_loop())


def main() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.connect(config.DB_PATH).close()
    app = Application.builder().token(config.env("BOT_TOKEN")).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("estado", estado))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot en marcha. Canales: %s", ", ".join(config.channels()))
    app.run_polling()


if __name__ == "__main__":
    main()
