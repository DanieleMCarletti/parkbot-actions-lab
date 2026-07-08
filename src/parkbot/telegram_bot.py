"""Long-running Telegram bot — the phone-facing input layer.

Commands:
  /start              pair (first run) or greet
  /help               usage
  /park  <data>       accoda parcheggio Milanofiori Nord
  /list               mostra la coda pendente
  /future             mostra prenotazioni confermate
  /fire               prova subito tutte le prenotazioni in coda
  /session            stato sessione (quando scade)
  /cancel <data>      rimuove dalla coda (non cancella prenotazioni già confermate)

Il bot ACCODA. Il fire notturno prenota e notifica il risultato.

NOTE 2026-06-11: l'integrazione ServiceNow (support-places.accenture.com) è
stata rimossa — il portale richiede MFA ~ogni 20 min (Conditional Access) e
non è automatizzabile. Parkbot ora usa solo Milanofiori Nord (Cognito).
"""
from __future__ import annotations

import json
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import booking, config, dateparse, queue, tokens


def _load_cfg() -> dict:
    if not config.TELEGRAM_CONFIG_FILE.exists():
        raise SystemExit(
            f"Missing {config.TELEGRAM_CONFIG_FILE}. Create it with your BotFather "
            f'token: {{"bot_token": "123:abc", "allowed_chat_id": null}}'
        )
    with config.TELEGRAM_CONFIG_FILE.open() as f:
        return json.load(f)


def _save_cfg(cfg: dict) -> None:
    tmp = config.TELEGRAM_CONFIG_FILE.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(config.TELEGRAM_CONFIG_FILE)


def _is_owner(cfg: dict, chat_id: int) -> bool:
    return cfg.get("allowed_chat_id") == chat_id


HELP = (
    "🅿️ <b>Parkbot Milanofiori Nord</b>\n\n"
    "<b>/park</b> &lt;data&gt; — accoda parcheggio\n"
    "   es: <code>/park domani</code>, <code>/park giovedì</code>, "
    "<code>/park 19/06/2026</code>\n\n"
    "<b>/list</b> — coda in attesa\n"
    "<b>/future</b> — prenotazioni confermate\n"
    "<b>/fire</b> — prova subito tutte le prenotazioni in coda (utile dopo cancellazioni)\n"
    "<b>/session</b> — stato sessione (quando scade)\n"
    "<b>/cancel</b> &lt;data&gt; — togli dalla coda\n\n"
    "La prenotazione parte da sola la prima notte utile e ti avviso col risultato."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    chat_id = update.effective_chat.id
    if cfg.get("allowed_chat_id") is None:
        cfg["allowed_chat_id"] = chat_id
        _save_cfg(cfg)
        await update.message.reply_text(
            f"✅ Associato! Chat <code>{chat_id}</code> registrata.\n\n{HELP}",
            parse_mode="HTML",
        )
        return
    if not _is_owner(cfg, chat_id):
        return
    await update.message.reply_text(HELP, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return
    await update.message.reply_text(HELP, parse_mode="HTML")


def _first_fire_label_iso(date_iso: str, booking_type: str = "parking") -> str:
    """Return 'prossima verifica' or 'dal GG/MM' for the first fire attempt.

    Milanofiori Nord has a ~3-day booking window, so a date more than 3 days
    out stays queued until it enters the window.
    """
    from datetime import date as _date, timedelta
    today = _date.today()
    try:
        target = _date.fromisoformat(date_iso)
    except ValueError:
        return "?"
    first = max(today, target - timedelta(days=3))
    if first <= today:
        return "prossima verifica"
    return f"dal {dateparse.to_display(first.isoformat())}"


async def cmd_park(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text(
            "Dimmi quando: <code>/park domani</code>", parse_mode="HTML"
        )
        return
    try:
        iso = dateparse.parse_date(" ".join(context.args))
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    _, mfn_new = queue.queue_booking(iso, "parking", source="telegram")
    if not mfn_new:
        await update.message.reply_text(
            f"⚠️ Parcheggio già in coda per <b>{dateparse.to_display(iso)}</b>.",
            parse_mode="HTML",
        )
        return

    fire = _first_fire_label_iso(iso, "parking")
    await update.message.reply_text(
        f"📌 In coda: parcheggio Milanofiori Nord per <b>{dateparse.to_display(iso)}</b>.\n"
        f"Prova: <b>{fire}</b>. Ti avviso appena prenotato (con posto).",
        parse_mode="HTML",
    )


def _first_fire_label(entry: "queue.QueueEntry") -> str:
    return _first_fire_label_iso(entry.date, entry.type)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return
    entries = queue.list_queue()
    if not entries:
        await update.message.reply_text("Coda vuota.")
        return
    lines = [
        f"🅿️ <b>{dateparse.to_display(e.date)}</b> — Milanofiori Nord"
        f" — prova: {_first_fire_label(e)}"
        for e in entries
    ]
    await update.message.reply_text(
        "<b>In coda:</b>\n" + "\n".join(lines), parse_mode="HTML"
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text(
            "Quale data? <code>/cancel domani</code>", parse_mode="HTML"
        )
        return
    try:
        iso = dateparse.parse_date(" ".join(context.args))
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    disp = dateparse.to_display(iso)
    path = config.QUEUE_DIR / f"{iso}-parking.json"
    if path.exists():
        path.unlink()
        await update.message.reply_text(
            f"🗑️ Rimosso dalla coda per {disp}.\n"
            f"(Nota: questo NON cancella una prenotazione già confermata sul portale.)"
        )
    else:
        await update.message.reply_text(
            f"Nessuna voce in coda per {disp}.\n"
            f"(Nota: /cancel non cancella prenotazioni già confermate.)"
        )


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return

    lines = ["<b>Stato sessione</b>\n"]

    # Milanofiori Nord (Cognito refresh_token)
    token_file = config.TOKEN_FILE
    if not token_file.exists():
        lines.append("🅿️ <b>Milanofiori Nord</b>: nessun token\n"
                     "   → <code>parkbot bootstrap</code>")
    else:
        import os, time as _time
        age_mfn_h = (_time.time() - os.path.getmtime(token_file)) / 3600
        h2 = int(age_mfn_h)
        lines.append(
            f"🅿️ <b>Milanofiori Nord</b>: ✅ token presente\n"
            f"   aggiornato {h2}h fa (refresh_token, durata lunga ~30g)"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_future(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return

    lines = []

    # Milanofiori Nord parking
    try:
        for b in booking.list_future_bookings():
            d = b.get("data") or b.get("date") or "?"
            spot = b.get("posti_codice") or "?"
            lines.append(f"🅿️ {dateparse.to_display(d)} — Milanofiori Nord, posto <b>{spot}</b>")
    except Exception as e:
        lines.append(f"⚠️ Milanofiori Nord: errore API ({e})")

    if not lines:
        await update.message.reply_text("Nessuna prenotazione confermata.")
        return
    await update.message.reply_text(
        "<b>Confermate:</b>\n" + "\n".join(lines), parse_mode="HTML"
    )


async def cmd_fire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return

    pending = queue.list_queue()
    if not pending:
        await update.message.reply_text("Coda vuota — niente da provare.")
        return

    items = ", ".join(
        f"🅿️ {dateparse.to_display(e.date)}" for e in pending
    )
    await update.message.reply_text(f"🔄 Verifica in corso…\n{items}")

    import asyncio
    from . import cli as _cli
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _cli._cmd_fire, None)

    # Post-fire summary: show what's still pending with reason.
    # Successes/failures are already notified by the fire itself.
    # This catches the "all full / out of window" case where fire is silent.
    still_pending = queue.list_queue()
    if not still_pending:
        return  # everything was booked — individual notifications already sent

    lines = []
    for e in still_pending:
        fire_lbl = _first_fire_label(e)
        lines.append(f"🅿️ <b>{dateparse.to_display(e.date)}</b> — ancora in attesa (prova: {fire_lbl})")

    await update.message.reply_text(
        "ℹ️ Verifica completata — nessuna novità:\n" + "\n".join(lines),
        parse_mode="HTML",
    )


async def on_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not _is_owner(cfg, update.effective_chat.id):
        return
    await update.message.reply_text("Comando sconosciuto. /help")


def main() -> int:
    cfg = _load_cfg()
    if not cfg.get("bot_token"):
        raise SystemExit("telegram.json has no bot_token.")

    app = Application.builder().token(cfg["bot_token"]).build()
    app.bot_data["cfg"] = cfg

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("park", cmd_park))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("fire", cmd_fire))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("future", cmd_future))
    app.add_handler(MessageHandler(filters.COMMAND, on_unknown))

    if cfg.get("allowed_chat_id") is None:
        print("PAIRING MODE: send /start to the bot from your phone to claim it.")
    else:
        print(f"Bot running. Owner chat_id={cfg['allowed_chat_id']}.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
