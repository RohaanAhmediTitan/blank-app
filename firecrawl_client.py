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


def _post_with_retry(url: str, json_body: dict, timeout: int) -> dict:
    """Shared throttle + retry/backoff for every Firecrawl POST, so both
    firecrawl_fetch_html and firecrawl_extract compete fairly for the same
    per-account rate limit instead of one of them (extract, previously)
    hitting the API with zero throttling or retries at all. That gap meant
    a single 429 from concurrent load failed instantly with a misleading
    generic error instead of retrying like the html path already did.

    Raises with Firecrawl's own error text on final failure — including the
    literal rate-limit message — instead of masking it, so callers (and
    their tooltips) show what actually happened.
    """
    global _last_request_time
    headers = {"Authorization": f"Bearer {_get_api_key()}", "Content-Type": "application/json"}

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        with _throttle_lock:
            elapsed = time.monotonic() - _last_request_time
            if elapsed < _MIN_START_SPACING_SECONDS:
                time.sleep(_MIN_START_SPACING_SECONDS - elapsed)
            _last_request_time = time.monotonic()

        try:
            resp = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if resp.status_code == 429:
                raise RuntimeError(f"Firecrawl rate limit (429): {resp.text[:300]}")
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                raise RuntimeError(body.get("error") or "Firecrawl reported failure with no error message")
            return body
        except Exception as exc:  # noqa: BLE001 - mirrors the TS catch-all + retry
            last_error = exc
            if attempt < _MAX_RETRIES:
                # Rate limits need real backoff, not just the thundering-herd
                # spacing above — 429s mean the account is already over
                # capacity, so back off harder than a one-off transient error.
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


def firecrawl_extract(url: str, schema: dict, prompt: str) -> dict | None:
    """LLM-based structured extraction from one page via Firecrawl.

    Uses /v2/scrape with a `json` format entry — the current, synchronous
    replacement for /v2/extract, which Firecrawl has deprecated (and which,
    as of 2026-09-21, was actively rejecting valid URLs with a bogus "All
    provided URLs are invalid" error on every call). This is how
    brand_scraper.py reads a rate off an arbitrary brand-direct booking
    engine without a hand-written parser per brand. Returns the extracted
    object, or raises if the page couldn't be scraped at all (e.g. a site
    whose bot defenses block Firecrawl outright, or a rate-limited account —
    both real "couldn't reach the page" failures, not "no rate found";
    brand_scraper.py's caller catches this and puts the message in the row's
    error field).
    """
    base = FIRECRAWL_API_URL.rsplit("/scrape", 1)[0]  # .../v2/scrape -> .../v2
    body = _post_with_retry(
        f"{base}/scrape",
        {
            "url": url,
            "formats": [{"type": "json", "schema": schema, "prompt": prompt}],
            "onlyMainContent": False,
        },
        timeout=60,
    )
    return body.get("data", {}).get("json")
