"""Daily check: re-read pages, detect openings, send deadline reminders; Sunday digest."""
import html
import logging
from datetime import date, datetime, timedelta

from telegram import Bot
from telegram.ext import ContextTypes

from . import config, db, extract, notify

log = logging.getLogger(__name__)


def today() -> date:
    return datetime.now(config.TZ).date()


def fmt_date(d: date) -> str:
    return d.strftime("%a %d %b %Y")


def describe(item, now: date) -> str:
    """One status line, e.g. '🟢 Open · closes Wed 05 Nov 2026 (23 days left)'."""
    if item["state"] == "applied":
        return "✅ Applied"
    if item["state"] == "dropped":
        return "❌ Dropped"
    close, opens = db.parse_date(item["close_date"]), db.parse_date(item["open_date"])
    if item["status"] in ("open", "unknown") and close:
        days = (close - now).days
        left = "closes TODAY" if days == 0 else f"{days} day{'s' * (days != 1)} left"
        label = "🟢 Open" if item["status"] == "open" else "❔ Status unclear"
        return f"{label} · closes {fmt_date(close)} ({left})"
    if item["status"] == "open":
        return "🟢 Open · deadline not found (set it with /deadline)"
    if item["status"] == "closed":
        if opens and opens >= now:
            return f"🔒 Not open yet · expected {fmt_date(opens)}"
        return "🔒 Closed · watching for it to open"
    return "❔ Status unclear · watching the page"


def card(item, now: date) -> str:
    e = html.escape
    lines = [f"<b>#{item['id']} · {e(item['title'])}</b>"]
    if item["summary"]:
        lines.append(f"<i>{e(item['summary'])}</i>")
    lines.append(describe(item, now))
    lines.append(f'<a href="{e(item["url"])}">Open link</a>')
    return "\n".join(lines)


def ensure_deadline(item, now: date):
    """Open or unclear items with no deadline get one DEFAULT_DEADLINE_DAYS from now."""
    if item["state"] != "watching" or item["status"] == "closed" or item["close_date"]:
        return item
    default = now + timedelta(days=config.DEFAULT_DEADLINE_DAYS)
    db.update_item(item["id"], close_date=default.isoformat())
    return db.get_item(item["id"])


async def refresh(bot: Bot, item, now: date, force: bool = False):
    """Re-read the page; tell you if it opened or the deadline moved. Returns the fresh row."""
    try:
        text = await extract.fetch_text(item["url"])
    except extract.FetchError as exc:
        log.info("#%s fetch failed: %s", item["id"], exc)
        return item
    stamp = datetime.now(config.TZ).isoformat(timespec="seconds")
    new_hash = extract.page_hash(text)
    if new_hash == item["page_hash"] and not force:
        db.update_item(item["id"], last_checked=stamp)
        return item

    result = await extract.analyze(item["url"], text, now)
    db.update_item(item["id"], page_hash=new_hash, last_checked=stamp, title=result["title"],
                   summary=result["summary"], status=result["status"],
                   # Keep a date you set by hand if the page stops mentioning it,
                   # but never carry over a deadline that has already passed.
                   open_date=result["open_date"] or item["open_date"],
                   close_date=result["close_date"] or (
                       item["close_date"] if (db.parse_date(item["close_date"]) or now) >= now else None))
    fresh = ensure_deadline(db.get_item(item["id"]), now)

    if item["status"] != "open" and fresh["status"] == "open":
        if db.mark_sent(fresh["id"], f"opened:{fresh['close_date'] or now.isoformat()}"):
            await notify.telegram(bot, "🟢 <b>Applications are OPEN</b>\n\n" + card(fresh, now),
                                  fresh["id"])
            await notify.email(f"🟢 Now open: {fresh['title']}",
                               notify.email_card("Applications are open", fresh,
                                                 f"<p>{html.escape(describe(fresh, now))}</p>"))
    elif fresh["status"] == "open" and fresh["close_date"] != item["close_date"] and item["close_date"]:
        await notify.telegram(bot, "📅 <b>Deadline changed</b>\n\n" + card(fresh, now), fresh["id"])
    return fresh


async def deadline_reminders(bot: Bot, item, now: date) -> None:
    close = db.parse_date(item["close_date"])
    if item["status"] not in ("open", "unknown") or not close:
        return
    days = (close - now).days

    if days < 0:
        db.update_item(item["id"], status="closed")
        if db.mark_sent(item["id"], f"passed:{close}"):
            await notify.telegram(bot, f"⌛ Deadline passed for <b>{html.escape(item['title'])}</b>. "
                                       "I'll keep watching the page for the next round.", item["id"])
        return

    if days not in config.EMAIL_DAYS + config.NUDGE_DAYS:
        return
    if not db.mark_sent(item["id"], f"d{days}:{close}"):
        return
    head = {0: "🚨 Closes TODAY", 1: "⏳ Closes TOMORROW"}.get(days, f"⏳ {days} days left")
    await notify.telegram(bot, f"<b>{head}</b> and you haven't applied yet\n\n" + card(item, now),
                          item["id"], snooze=True)
    if days in config.EMAIL_DAYS:
        await notify.email(f"{head}: {item['title']}",
                           notify.email_card(f"{head}, not applied yet", item,
                                             f"<p>Deadline: <b>{fmt_date(close)}</b></p>"))


async def check_item(bot: Bot, item, now: date) -> None:
    item = ensure_deadline(await refresh(bot, item, now), now)
    opens = db.parse_date(item["open_date"])
    if item["status"] != "open" and opens == now and db.mark_sent(item["id"], f"expected:{opens}"):
        await notify.telegram(bot, "📅 <b>Expected to open today</b>, but the page doesn't show it "
                                   "yet. Worth a manual look.\n\n" + card(item, now), item["id"])
    await deadline_reminders(bot, item, now)
    if item["remind_on"] and db.parse_date(item["remind_on"]) <= now:
        db.update_item(item["id"], remind_on=None)
        await notify.telegram(bot, "⏰ <b>Snoozed reminder</b>\n\n" + card(item, now),
                              item["id"], snooze=True)


async def daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = today()
    for item in db.items("watching"):
        try:
            await check_item(context.bot, item, now)
        except Exception:  # one bad page shouldn't stop the rest
            log.exception("Daily check failed for #%s", item["id"])


async def weekly_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = today()
    watching = db.items("watching")
    if not watching:
        return
    is_open = sorted((i for i in watching if i["status"] == "open"),
                     key=lambda i: i["close_date"] or "9999")
    waiting = [i for i in watching if i["status"] != "open"]
    week_end = now + timedelta(days=7)
    closing = [i for i in is_open if (d := db.parse_date(i["close_date"])) and d <= week_end]

    def rows(items_):
        return "".join(
            f'<li style="margin-bottom:6px"><a href="{html.escape(i["url"])}">{html.escape(i["title"])}</a>'
            f'<br><span style="color:#555">{html.escape(describe(i, now))}</span></li>' for i in items_)

    body = '<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:560px">'
    body += f"<h2>Your week: {len(is_open)} open, {len(closing)} closing in 7 days</h2>"
    if is_open:
        body += f"<h3>🟢 Open, not applied</h3><ul>{rows(is_open)}</ul>"
    if waiting:
        body += f"<h3>🔒 Watching for opening</h3><ul>{rows(waiting)}</ul>"
    body += f'<p style="color:#888">Applied so far: {len(db.items("applied"))}</p></div>'
    await notify.email(f"📋 Weekly: {len(closing)} closing this week, {len(is_open)} open", body)
