import html
import logging

import httpx
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from . import config

log = logging.getLogger(__name__)


def item_buttons(item_id: int, snooze: bool = False) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton("✅ Applied", callback_data=f"applied:{item_id}")]
    if snooze:
        row.append(InlineKeyboardButton("⏰ Tomorrow", callback_data=f"snooze:{item_id}"))
    row.append(InlineKeyboardButton("❌ Drop", callback_data=f"drop:{item_id}"))
    return InlineKeyboardMarkup([row])


async def telegram(bot: Bot, text: str, item_id: int | None = None, snooze: bool = False) -> None:
    await bot.send_message(
        config.OWNER_CHAT_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=item_buttons(item_id, snooze) if item_id else None,
    )


async def email(subject: str, html_body: str) -> bool:
    if not (config.RESEND_API_KEY and config.EMAIL_TO):
        log.warning("Email skipped: RESEND_API_KEY / EMAIL_TO not set")
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
                json={"from": config.EMAIL_FROM, "to": config.EMAIL_TO,
                      "reply_to": config.EMAIL_TO[0], "subject": subject, "html": html_body,
                      # A plain-text part makes spam filters trust the message more.
                      "text": BeautifulSoup(html_body, "html.parser").get_text("\n", strip=True)},
            )
    except httpx.HTTPError as exc:
        log.error("Resend request failed: %s", exc)
        return False
    if resp.status_code >= 300:
        log.error("Resend returned %s: %s", resp.status_code, resp.text[:300])
        return False
    return True


def email_card(heading: str, item, extra: str = "") -> str:
    e = html.escape
    return f"""<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:520px">
<h2 style="margin:0 0 8px">{e(heading)}</h2>
<p style="margin:0 0 4px;font-size:16px"><b>{e(item['title'])}</b></p>
<p style="margin:0 0 12px;color:#555">{e(item['summary'])}</p>
{extra}
<p><a href="{e(item['url'])}" style="background:#2563eb;color:#fff;padding:10px 16px;
border-radius:6px;text-decoration:none">Open application</a></p>
<p style="color:#888;font-size:12px">Applied or not interested? Tap the buttons on the Telegram message.</p>
</div>"""
