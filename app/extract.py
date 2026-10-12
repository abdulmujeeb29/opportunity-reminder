"""Fetch an application page and ask the model what it says about status and dates."""
import hashlib
import json
import re
from datetime import date

import httpx
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

from . import config

MAX_PAGE_CHARS = 60_000
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/141.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

SYSTEM = """You read web pages for opportunities people apply to (scholarships, fellowships,
jobs, grants, programs, competitions, visas, conferences) and report the application window.

Rules:
- status: "open" if applications are being accepted right now, "closed" if the page says
  they are closed / not yet open / opening later, "unknown" if the page doesn't say.
- open_date / close_date: YYYY-MM-DD for the CURRENT or NEXT cycle only. Never return a date
  from a past cycle as close_date. If only a month is given, use the last day of that month
  for close_date and the first day for open_date. Use null when not stated.
- If the deadline has passed relative to today, status is "closed".
- title: short name of the opportunity, including the year/cycle if shown.
- summary: one short line (who it's for / what it is). No dates in the summary."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "status": {"type": "string", "enum": ["open", "closed", "unknown"]},
        "open_date": {"type": ["string", "null"]},
        "close_date": {"type": ["string", "null"]},
    },
    "required": ["title", "summary", "status", "open_date", "close_date"],
    "additionalProperties": False,
}

_client: AsyncOpenAI | None = None


def _llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.AZURE_OPENAI_API_KEY,
                              base_url=config.model_base_url(), timeout=90)
    return _client


class FetchError(Exception):
    pass


async def fetch_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as http:
            resp = await http.get(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"couldn't reach the page ({exc.__class__.__name__})") from None
    if resp.status_code >= 400:
        raise FetchError(f"the page returned HTTP {resp.status_code}")
    if "html" not in resp.headers.get("content-type", "html"):
        raise FetchError(f"the link isn't a web page ({resp.headers['content-type']})")

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = re.sub(r"\n\s*\n+", "\n", soup.get_text("\n"))
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 200:
        raise FetchError("the page has almost no readable text (it may need JavaScript or a login)")
    return f"Page title: {title}\n\n{text}"[:MAX_PAGE_CHARS]


def page_hash(text: str) -> str:
    # Digits-insensitive so view counters / timestamps don't count as changes.
    return hashlib.sha256(re.sub(r"\d", "", text).encode()).hexdigest()


async def analyze(url: str, text: str, today: date) -> dict:
    response = await _llm().responses.create(
        model=config.AZURE_OPENAI_MODEL,
        instructions=SYSTEM,
        input=f"Today is {today.isoformat()}.\nURL: {url}\n\n{text}",
        store=False,
        max_output_tokens=2000,
        text={"format": {"type": "json_schema", "name": "application_window",
                         "strict": True, "schema": SCHEMA}},
    )
    result = json.loads(response.output_text)
    for key in ("open_date", "close_date"):
        try:
            result[key] = date.fromisoformat(result[key]).isoformat() if result[key] else None
        except ValueError:
            result[key] = None
    return result
