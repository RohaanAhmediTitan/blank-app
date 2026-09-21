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


def firecrawl_extract(urls: list[str], schema: dict, prompt: str, poll_timeout: int = 90) -> dict | None:
    """Submit a Firecrawl /v2/extract job and poll until it completes.

    Extract is LLM-based (unlike /v2/scrape's raw HTML we parse ourselves),
    so it's the practical way to read a rate off an arbitrary brand-direct
    booking engine without a hand-written parser per brand — see
    brand_scraper.py. Returns the extracted `data` object, or None on
    failure/timeout (caller treats that as "not available").
    """
    base = FIRECRAWL_API_URL.rsplit("/scrape", 1)[0]  # .../v2/scrape -> .../v2
    headers = {"Authorization": f"Bearer {_get_api_key()}", "Content-Type": "application/json"}

    resp = requests.post(
        f"{base}/extract",
        headers=headers,
        json={"urls": urls, "prompt": prompt, "schema": schema, "scrapeOptions": {"onlyMainContent": False}},
        timeout=30,
    )
    resp.raise_for_status()
    job = resp.json()
    if not job.get("success") or not job.get("id"):
        return None

    job_id = job["id"]
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        time.sleep(3)
        poll = requests.get(f"{base}/extract/{job_id}", headers=headers, timeout=30)
        poll.raise_for_status()
        result = poll.json()
        status = result.get("status")
        if status == "completed":
            return result.get("data")
        if status in ("failed", "cancelled"):
            return None
    return None
