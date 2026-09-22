"""Minimal Firecrawl client, ported from pricepoint-backend's firecrawl-throttler.ts.

POST to /v2/scrape for rendered HTML, with 3 retries and backoff on failure.

Rate limiting: live-tested 2026-09-22 and hit a real 429 from Firecrawl with
"Consumed (req/min): 18, Remaining (req/min): 0" — the account's plan caps at
18 requests/minute. `_RateLimiter` below enforces a sliding-window cap under
that (see _REQUESTS_PER_MINUTE) proactively, so normal use shouldn't trip the
429 at all rather than just retrying after the fact.

Retry policy distinguishes worth-retrying (429, timeouts, generic 5xx — all
transient) from not-worth-retrying (Firecrawl's own SCRAPE_ALL_ENGINES_FAILED
code, confirmed live to mean "this site's bot defenses block us," e.g.
marriott.com — retrying that 3x just burns 3x the credits for a result we
already know won't change).
"""

import os
import threading
import time
from collections import deque

import requests
from dotenv import load_dotenv

load_dotenv()

FIRECRAWL_API_URL = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2/scrape")
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5.0

# Firecrawl's own cap is 18/min (confirmed live) — stay under it with margin
# rather than aim right at the edge, since a burst of near-simultaneous
# requests from multiple worker threads can overshoot a naive check by a
# request or two.
_REQUESTS_PER_MINUTE = 14

# Error codes Firecrawl returns for a page it structurally can't reach —
# not a rate limit, not a transient hiccup, and retrying won't help.
_NON_RETRYABLE_CODES = {"SCRAPE_ALL_ENGINES_FAILED"}


class _RateLimiter:
    """Sliding-window limiter shared by every call this process makes, so
    firecrawl_fetch_html and firecrawl_extract (different call sites, same
    Firecrawl account) can't collectively exceed the plan's real cap."""

    def __init__(self, max_per_minute: int):
        self._max = max_per_minute
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def wait_for_slot(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] > 60:
                    self._calls.popleft()
                if len(self._calls) < self._max:
                    self._calls.append(now)
                    return
                sleep_for = 60 - (now - self._calls[0]) + 0.05
            time.sleep(max(sleep_for, 0.05))


_rate_limiter = _RateLimiter(_REQUESTS_PER_MINUTE)


class _NonRetryable(Exception):
    """A Firecrawl failure known not to improve on retry — see _NON_RETRYABLE_CODES."""


def _get_api_key() -> str:
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError(
            "FIRECRAWL_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or set the environment variable directly."
        )
    return key


def _post_with_retry(url: str, json_body: dict, timeout: int) -> dict:
    """Shared rate-limiting + retry/backoff for every Firecrawl POST, so both
    firecrawl_fetch_html and firecrawl_extract compete fairly for the same
    per-account rate limit instead of one of them (extract, previously)
    hitting the API with zero throttling or retries at all.

    Raises with Firecrawl's own error text on final failure — including the
    literal rate-limit message — instead of masking it, so callers (and
    their tooltips) show what actually happened.
    """
    headers = {"Authorization": f"Bearer {_get_api_key()}", "Content-Type": "application/json"}

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        _rate_limiter.wait_for_slot()

        try:
            resp = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if resp.status_code == 429:
                raise RuntimeError(f"Firecrawl rate limit (429): {resp.text[:300]}")
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                code = body.get("code")
                message = body.get("error") or "Firecrawl reported failure with no error message"
                if code in _NON_RETRYABLE_CODES:
                    raise _NonRetryable(message)
                raise RuntimeError(message)
            return body
        except _NonRetryable:
            raise  # skip the retry loop entirely — see class docstring
        except Exception as exc:  # noqa: BLE001 - mirrors the TS catch-all + retry
            last_error = exc
            if attempt < _MAX_RETRIES:
                # Rate limits need real backoff on top of the proactive
                # limiter above (e.g. another process sharing this key) —
                # back off harder than a one-off transient error.
                delay = _RETRY_DELAY_SECONDS * (attempt + 1) * (3 if "429" in str(exc) else 1)
                time.sleep(delay)

    assert last_error is not None
    raise last_error


def firecrawl_fetch_html(url: str, wait_for: int | None = None, max_age: int | None = None) -> str:
    """Fetch rendered HTML for a URL via Firecrawl, throttled and retried like the production scraper."""
    body: dict = {"url": url, "onlyMainContent": False, "formats": ["html"]}
    if wait_for is not None:
        body["waitFor"] = wait_for
    if max_age is not None:
        body["maxAge"] = max_age

    result = _post_with_retry(FIRECRAWL_API_URL, body, timeout=60)
    html = result.get("data", {}).get("html")
    if not html:
        raise RuntimeError(result.get("error") or "Firecrawl returned no HTML")
    return html


def firecrawl_extract(url: str, schema: dict, prompt: str) -> tuple[dict | None, str]:
    """LLM-based structured extraction from one page via Firecrawl.

    Uses /v2/scrape with a `json` format entry — the current, synchronous
    replacement for /v2/extract, which Firecrawl has deprecated (and which,
    as of 2026-09-21, was actively rejecting valid URLs with a bogus "All
    provided URLs are invalid" error on every call). This is how
    brand_scraper.py reads a rate off an arbitrary brand-direct booking
    engine without a hand-written parser per brand.

    Also requests `markdown` in the same call and returns it alongside the
    extracted object, so the caller can verify the extraction is actually
    grounded in real page text (confirmed necessary: live-tested on a page
    with zero pricing content and the model still confidently fabricated a
    room rate — see brand_scraper.py's evidence check).

    Raises if the page couldn't be scraped at all (e.g. a site whose bot
    defenses block Firecrawl outright, or a rate-limited account — both real
    "couldn't reach the page" failures, not "no rate found"; brand_scraper.py's
    caller catches this and puts the message in the row's error field).
    """
    base = FIRECRAWL_API_URL.rsplit("/scrape", 1)[0]  # .../v2/scrape -> .../v2
    body = _post_with_retry(
        f"{base}/scrape",
        {
            "url": url,
            "formats": [{"type": "json", "schema": schema, "prompt": prompt}, "markdown"],
            "onlyMainContent": False,
        },
        timeout=60,
    )
    data = body.get("data", {})
    return data.get("json"), data.get("markdown") or ""
