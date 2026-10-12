# Opportunity Reminder

A Telegram bot that tracks application links. Send it a link and it reads the page with Azure `gpt-5.6-terra-1`, then:

- **Open:** reminds you before the deadline until you tap ✅ Applied.
  Telegram at 14/10/5/2 days left; Telegram **and** email at 7/3/1 days and on deadline day.
- **Closed / not open yet:** re-reads the page daily and alerts you (Telegram + email) when it opens.
- **Sunday 18:00:** a weekly email digest.

Times follow `TIMEZONE` (default Africa/Kigali). The daily check runs at `REMINDER_HOUR` (default 08:00).

## Setup

1. Create a bot with @BotFather (`/newbot`) and copy the token.
2. Fill in `.env` (copy it from `.env.example`). Leave `OWNER_CHAT_ID` empty for now.
3. Run the bot, send it `/start`, and it replies with your chat ID. Put that in `OWNER_CHAT_ID` and restart. After that it only answers you.
4. Send `/testemail` to check Resend.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.bot
```

## Deploy (Docker)

```bash
docker build -t opportunity-reminder .
docker run -d --name opportunity-reminder --restart unless-stopped \
  --env-file .env -v opportunity-reminder-data:/data opportunity-reminder
```

Mount `/data`: the SQLite database lives there. Run **one** instance only (Telegram polling).

## Email sender

`onboarding@resend.dev` works immediately but only delivers to your Resend account's address.
For a proper sender, verify `mail.thalesops.com` in Resend and set
`EMAIL_FROM=⏰ Opportunity Reminder <remind@mail.thalesops.com>`.

## Commands

`/list` · `/done` · `/deadline <id> <YYYY-MM-DD>` · `/opens <id> <YYYY-MM-DD>` · `/check <id>` · `/run` · `/testemail`

Some sites block bots or need JavaScript. The bot still saves those links, and you set the dates yourself with `/deadline` or `/opens`.
