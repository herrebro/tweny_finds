"""Bot de Telegram: escribe un nombre o manda una foto y devuelve artículos de los canales.
Un solo proceso: el bot contesta y, en segundo plano, vuelve a leer los canales cada cierto tiempo.

Acceso: los ids de ALLOWED_USERS son los dueños (siempre pueden usarlo y aprueban a los demás).
Cualquier otra persona que le escriba queda pendiente y al dueño le llega un aviso con botones Aprobar/Rechazar.
"""
import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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


def is_owner(user_id: int) -> bool:
    return user_id in config.allowed_users()


async def authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not config.allowed_users():
        await update.message.reply_text(
            f"Tu id de Telegram es {user.id}. Ponlo en la variable ALLOWED_USERS y reinicia el bot."
        )
        return False
    if is_owner(user.id):
        return True
    status = await asyncio.to_thread(_with_conn, db.user_status, user.id)
    if status == "approved":
        return True
    if status == "denied":
        return False  # se ignora en silencio
    if status is None:
        name = user.full_name + (f" (@{user.username})" if user.username else "")
        await asyncio.to_thread(_with_conn, db.set_user_status, user.id, "pending", name)
        buttons = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Aprobar", callback_data=f"ok:{user.id}"),
              InlineKeyboardButton("Rechazar", callback_data=f"no:{user.id}")]]
        )
        for owner in config.allowed_users():
            try:
                await context.bot.send_message(owner, f"{name} (id {user.id}) quiere usar el bot.", reply_markup=buttons)
            except Exception:
                log.exception("No se pudo avisar al dueño %s", owner)
    await update.message.reply_text("Todavía no tienes permiso. He avisado al dueño y te escribiré cuando te apruebe.")
    return False


async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer("No tienes permiso para esto", show_alert=True)
        return
    action, _, raw_id = query.data.partition(":")
    target = int(raw_id)
    approved = action == "ok"
    await asyncio.to_thread(_with_conn, db.set_user_status, target, "approved" if approved else "denied")
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n→ {'Aprobado' if approved else 'Rechazado'}")
    if approved:
        try:
            await context.bot.send_message(target, "Ya puedes usar el bot. Escríbeme un nombre o mándame una foto.")
        except Exception:
            log.exception("No se pudo avisar al usuario aprobado %s", target)


async def usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    rows = await asyncio.to_thread(_with_conn, db.list_users, "approved")
    if not rows:
        await update.message.reply_text("No hay usuarios aprobados aparte de ti.")
        return
    lines = [f"{r['name'] or 'sin nombre'} — id {r['user_id']}" for r in rows]
    await update.message.reply_text("Usuarios aprobados:\n" + "\n".join(lines) + "\n\nPara quitar a alguien: /quitar <id>")


async def quitar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Uso: /quitar <id>. Los ids salen en /usuarios.")
        return
    await asyncio.to_thread(_with_conn, db.set_user_status, int(context.args[0]), "denied")
    await update.message.reply_text("Hecho: ya no puede usar el bot.")


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
    if not await authorized(update, context):
        return
    extra = "\n/estado, /usuarios y /quitar son solo para ti." if is_owner(update.effective_user.id) else ""
    await update.message.reply_text(
        "Escríbeme un nombre o una marca, o mándame una foto, y te devuelvo artículos de los canales." + extra
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
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text(await asyncio.to_thread(_with_conn, _status_text))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update, context):
        return
    results = await asyncio.to_thread(_with_conn, search.search_text, update.message.text, MAX_RESULTS)
    await send_results(update, results, with_score=False)


def _image_search(conn, path: str) -> list[dict]:
    import embed  # el modelo solo se carga cuando hace falta

    return search.search_image(conn, embed.embed_image(path), MAX_RESULTS)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update, context):
        return
    tg_file = await update.message.photo[-1].get_file()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "query.jpg"
        await tg_file.download_to_drive(str(path))
        try:
            results = await asyncio.to_thread(_with_conn, _image_search, str(path))
        except Exception:
            log.exception("fallo en la búsqueda por foto")
            await update.message.reply_text("No he podido procesar la foto ahora mismo. Inténtalo en unos minutos.")
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
    app.add_handler(CommandHandler("usuarios", usuarios))
    app.add_handler(CommandHandler("quitar", quitar))
    app.add_handler(CallbackQueryHandler(on_decision, pattern=r"^(ok|no):\d+$"))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot en marcha. Canales: %s", ", ".join(config.channels()))
    app.run_polling()


if __name__ == "__main__":
    main()
