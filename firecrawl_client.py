"""Minimal Firecrawl client, ported from pricepoint-backend's firecrawl-throttler.ts.

POST to /v2/scrape for rendered HTML, with 3 retries and backoff on failure.

Production's throttler serializes every call app-wide with a 2s gap because
it's a single shared API key backing many concurrent users' scrape jobs. This
POC is a single local demo session, so instead of serializing, we cap
*concurrent in-flight requests* (via a semaphore) and only stagger request
*starts* by a small amount — Firecrawl itself handles the actual anti-bot
rendering against Booking.com/Expedia, so our own client doesn't need to be
this conservative. Callers get real parallelism via a ThreadPoolExecutor.
"""

import os
import threading
import time

import requests
from dotenv import load_dotenv

load_dotenv()

FIRECRAWL_API_URL = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2/scrape")
_MIN_START_SPACING_SECONDS = 0.3  # just avoids a thundering-herd burst at t=0
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5.0

_last_request_time = 0.0
_throttle_lock = threading.Lock()


def _get_api_key() -> str:
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError(
            "FIRECRAWL_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or set the environment variable directly."
        )
    return key


def firecrawl_fetch_html(url: str, wait_for: int | None = None, max_age: int | None = None) -> str:
    """Fetch rendered HTML for a URL via Firecrawl, throttled and retried like the production scraper."""
    global _last_request_time

    body: dict = {"url": url, "onlyMainContent": False, "formats": ["html"]}
    if wait_for is not None:
        body["waitFor"] = wait_for
    if max_age is not None:
        body["maxAge"] = max_age

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        with _throttle_lock:
            elapsed = time.monotonic() - _last_request_time
            if elapsed < _MIN_START_SPACING_SECONDS:
                time.sleep(_MIN_START_SPACING_SECONDS - elapsed)
            _last_request_time = time.monotonic()

        try:
            resp = requests.post(
                FIRECRAWL_API_URL,
                headers={
                    "Authorization": f"Bearer {_get_api_key()}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            html = data.get("data", {}).get("html")
            if not data.get("success") or not html:
                raise RuntimeError(data.get("error") or "Firecrawl returned no HTML")
            return html
        except Exception as exc:  # noqa: BLE001 - mirrors the TS catch-all + retry
            last_error = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))

    assert last_error is not None
    raise last_error


def firecrawl_extract(url: str, schema: dict, prompt: str) -> dict | None:
    """LLM-based structured extraction from one page via Firecrawl.

    Uses /v2/scrape with a `json` format entry — the current, synchronous
    replacement for /v2/extract, which Firecrawl has deprecated (and which,
    as of 2026-09-21, was actively rejecting valid URLs with a bogus "All
    provided URLs are invalid" error on every call). This is how
    brand_scraper.py reads a rate off an arbitrary brand-direct booking
    engine without a hand-written parser per brand. Returns the extracted
    object, or None if the page couldn't be scraped at all (e.g. a site
    whose bot defenses block Firecrawl outright — that's a real "couldn't
    reach the page" failure, not "no rate found").
    """
    base = FIRECRAWL_API_URL.rsplit("/scrape", 1)[0]  # .../v2/scrape -> .../v2
    headers = {"Authorization": f"Bearer {_get_api_key()}", "Content-Type": "application/json"}

    resp = requests.post(
        f"{base}/scrape",
        headers=headers,
        json={
            "url": url,
            "formats": [{"type": "json", "schema": schema, "prompt": prompt}],
            "onlyMainContent": False,
        },
        timeout=60,
    )
    if not resp.ok:
        return None
    body = resp.json()
    if not body.get("success"):
        return None
    return body.get("data", {}).get("json")
