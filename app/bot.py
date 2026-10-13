import html
import logging
import re
from datetime import time, timedelta

from openai import OpenAIError
from telegram import MessageEntity, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import config, db, extract, notify
from .reminders import card, daily_job, ensure_deadline, refresh, today, weekly_digest

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")
URL_RE = re.compile(r"https?://[^\s<>\"']+")

HELP = """<b>Send me any application link</b> and I'll track it.

• Open → I remind you at 14, 10, 7, 5, 3, 2, 1 days and on the day (7/3/1/day-of also by email)
• No deadline on the page → I set one 5 days out
• Closed → I check the page daily and tell you when it opens
• Tap ✅ Applied to stop reminders

<b>Commands</b>
/list – what you're tracking
/done – what you've applied to
/deadline <i>id</i> <i>YYYY-MM-DD</i> – set the closing date yourself
/opens <i>id</i> <i>YYYY-MM-DD</i> – set when it opens
/check <i>id</i> – re-read the page now
/run – run today's check now
/testemail – send a test email"""


def reply(update: Update, text: str, **kw):
    return update.effective_message.reply_text(text, parse_mode=ParseMode.HTML,
                                               disable_web_page_preview=True, **kw)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id != config.OWNER_CHAT_ID:
        log.info("Message from non-owner chat %s", chat_id)
        await reply(update, f"Your chat ID is <code>{chat_id}</code>.\n"
                            "Put it in <code>OWNER_CHAT_ID</code> and restart me.")
        return
    await reply(update, HELP)


async def add_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    urls = list(msg.parse_entities([MessageEntity.URL]).values())
    urls += [e.url for e in msg.entities or [] if e.type == MessageEntity.TEXT_LINK]
    urls += URL_RE.findall(msg.text or "")
    # Split "a.com/x,https://b.com" glued together, and drop trailing punctuation.
    urls = [u.strip(".,;)]") for raw in urls for u in re.split(r",(?=\s*(?:https?://|www\.))", raw)]
    urls = [u for u in urls if u]
    if not urls:
        await reply(update, "Send me a link to an application and I'll track it. /help")
        return
    now = today()
    for url in dict.fromkeys(u if "://" in u else f"https://{u}" for u in urls):
        existing = next((i for i in db.items() if i["url"] == url and i["state"] != "dropped"), None)
        if existing:
            await reply(update, "Already tracking this one.\n\n" + card(existing, now))
            continue

        status = await reply(update, "🔎 Reading the page…")
        try:
            text = await extract.fetch_text(url)
            result = await extract.analyze(url, text, now)
        except (extract.FetchError, OpenAIError, ValueError) as exc:
            reason = exc if isinstance(exc, extract.FetchError) else "couldn't analyse the page"
            item = ensure_deadline(db.get_item(db.add_item(url=url, title=url, status="unknown")), now)
            item_id = item["id"]
            await status.edit_text(
                f"⚠️ Saved as #{item_id}, but {html.escape(str(reason))}.\n"
                f"I set a deadline {config.DEFAULT_DEADLINE_DAYS} days from now so you still get reminders.\n\n"
                f"Fix the dates yourself:\n/deadline {item_id} 2026-12-31\n/opens {item_id} 2027-01-15",
                parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            continue

        item_id = db.add_item(url=url, page_hash=extract.page_hash(text), **result)
        item = ensure_deadline(db.get_item(item_id), now)
        hint = (f"\n\n<i>No deadline on the page, so I set one {config.DEFAULT_DEADLINE_DAYS} days from now.</i>"
                f" Change it with /deadline {item_id} YYYY-MM-DD"
                if not result["close_date"] and item["close_date"] else "")
        await status.edit_text("📌 Tracking\n\n" + card(item, now) + hint, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True, reply_markup=notify.item_buttons(item_id))


async def list_items(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = db.items("watching")
    if not items:
        await reply(update, "Nothing tracked yet. Send me a link.")
        return
    items.sort(key=lambda i: (i["status"] != "open", i["close_date"] or "9999"))
    now = today()
    await reply(update, "\n\n".join(card(i, now) for i in items))


async def list_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = db.items("applied")
    now = today()
    await reply(update, "\n\n".join(card(i, now) for i in items) if items else "Nothing marked applied yet.")


def _item_and_date(context: ContextTypes.DEFAULT_TYPE):
    try:
        item = db.get_item(int(context.args[0]))
        when = db.parse_date(context.args[1]) if len(context.args) > 1 else None
    except (IndexError, ValueError):
        return None, None
    return item, when


async def set_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    item, when = _item_and_date(context)
    if not item or not when:
        await reply(update, "Usage: /deadline <i>id</i> <i>YYYY-MM-DD</i>")
        return
    status = "open" if when >= today() else "closed"
    db.update_item(item["id"], close_date=when.isoformat(), status=status)
    await reply(update, "Updated.\n\n" + card(db.get_item(item["id"]), today()))


async def set_opens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    item, when = _item_and_date(context)
    if not item or not when:
        await reply(update, "Usage: /opens <i>id</i> <i>YYYY-MM-DD</i>")
        return
    db.update_item(item["id"], open_date=when.isoformat(),
                   status="closed" if when > today() else item["status"])
    await reply(update, "Updated. I'll ping you that day.\n\n" + card(db.get_item(item["id"]), today()))


async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    item, _ = _item_and_date(context)
    if not item:
        await reply(update, "Usage: /check <i>id</i>")
        return
    try:
        item = await refresh(context.bot, item, today(), force=True)
    except (OpenAIError, ValueError):
        await reply(update, "Couldn't analyse the page right now. Try again later.")
        return
    await reply(update, card(item, today()), reply_markup=notify.item_buttons(item["id"]))


async def run_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, "Running today's check…")
    await daily_job(context)
    await reply(update, "Done.")


async def test_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok = await notify.email(
        "Opportunity Reminder: email is working",
        "<p>Hi,</p><p>This is a test from your Opportunity Reminder bot. From now on you'll get "
        "an email when an application you're tracking opens, and 7 days, 3 days, 1 day and on "
        "the day before it closes, plus a weekly summary on Sundays.</p>"
        "<p>Mark applications as applied in Telegram to stop the reminders.</p>")
    await reply(update, "Sent. Check your inbox." if ok else "Email failed. Check the logs and your Resend settings.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.message.chat.id != config.OWNER_CHAT_ID:
        await query.answer()
        return
    action, item_id = query.data.split(":")
    item = db.get_item(int(item_id))
    if not item:
        await query.answer("That item no longer exists.")
        return
    if action == "snooze":
        db.update_item(item["id"], remind_on=(today() + timedelta(days=1)).isoformat())
        await query.answer("I'll remind you tomorrow.")
        return
    db.update_item(item["id"], state="applied" if action == "applied" else "dropped")
    note = "✅ Marked as applied. No more reminders." if action == "applied" else "❌ Dropped."
    await query.answer(note)
    await query.edit_message_text(query.message.text_html + f"\n\n<b>{note}</b>",
                                  parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
    db.init()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    if not config.OWNER_CHAT_ID:
        log.warning("OWNER_CHAT_ID not set: replying to everyone with their chat ID only")
        app.add_handler(MessageHandler(filters.ALL, start))
    else:
        owner = filters.Chat(chat_id=config.OWNER_CHAT_ID)
        for name, fn in [("help", start), ("list", list_items), ("done", list_done),
                         ("deadline", set_deadline), ("opens", set_opens), ("check", check_now),
                         ("run", run_now), ("testemail", test_email)]:
            app.add_handler(CommandHandler(name, fn, filters=owner))
        app.add_handler(CallbackQueryHandler(on_button))
        app.add_handler(MessageHandler(owner & ~filters.COMMAND, add_links))

        jobs = app.job_queue
        jobs.run_daily(daily_job, time(config.REMINDER_HOUR, 0, tzinfo=config.TZ), name="daily")
        jobs.run_daily(weekly_digest, time(config.DIGEST_HOUR, 0, tzinfo=config.TZ),
                       days=(0,), name="digest")  # 0 = Sunday
        log.info("Daily check at %02d:00 and Sunday digest at %02d:00 (%s)",
                 config.REMINDER_HOUR, config.DIGEST_HOUR, config.TZ.key)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
