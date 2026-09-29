"""SerpApi Google Hotels client — brand.com ("official site") rate lookup.

Replaces the earlier Firecrawl-based approach in brand_scraper.py, which hit
two dead ends: Marriott blocks Firecrawl's render engines outright
(SCRAPE_ALL_ENGINES_FAILED on every attempt), and IHG's booking flow is a
login-gated shell Firecrawl can render but that has no room results ever
loaded. SerpApi returns Google's own structured price data instead of an LLM
reading a rendered page, so there's no hallucination risk to guard against
here, and Google's crawl reaches sites Firecrawl's engines can't (confirmed
live 2026-09-29: Marriott shows up fine through this path).

Response shape, confirmed live against both demo hotels (2026-09-29):
  - A specific enough query (name + full address) sometimes gets Google to
    resolve straight to one property, returning its full detail payload —
    including a top-level `prices` array — in a single call.
  - A less specific query instead returns a `properties` list (multiple
    candidates), each with only a `property_token`; getting `prices` then
    needs a second call with that token (and the same `q`, which SerpApi
    requires even for a token-based lookup).
  - Each entry in `prices` covers one source (an OTA, a deals aggregator,
    or the brand's own site) with a `rate_per_night`. The brand's own
    listing is marked `"official": true` — confirmed present the same way
    for both Marriott and IHG — which is a far more reliable way to find it
    than matching brand name/domain against `source`, since `source` for the
    official entry is just the property's own display name (e.g. "Fairfield
    by Marriott Inn & Suites..."), not a domain or brand string.

Coverage is inherently partial: a miss here means Google's hotel-price feed
doesn't have an official listing for this property/date, not necessarily
that no rate exists.
"""

import os
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

SERPAPI_URL = "https://serpapi.com/search.json"


def _get_api_key() -> str:
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        raise RuntimeError(
            "SERPAPI_KEY is not set. Copy .env.example to .env and add your key, "
            "or set the environment variable directly."
        )
    return key


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _query(q: str, check_in: date, check_out: date, guests: int, property_token: str | None = None) -> dict:
    params = {
        "engine": "google_hotels",
        "q": q,
        "check_in_date": check_in.isoformat(),
        "check_out_date": check_out.isoformat(),
        "adults": guests,
        "currency": "USD",
        "gl": "us",
        "hl": "en",
        "api_key": _get_api_key(),
    }
    if property_token:
        params["property_token"] = property_token
    resp = requests.get(SERPAPI_URL, params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(f"SerpApi: {body['error']}")
    return body


def _resolve_prices(
    hotel_name: str, address: str, check_in: date, check_out: date, guests: int
) -> tuple[list[dict], str]:
    """Returns (prices, matched_name). prices is empty if nothing matched
    confidently enough to trust — a bad match here would silently attribute
    a different hotel's rate to this one."""
    q = f"{hotel_name} {address}"
    body = _query(q, check_in, check_out, guests)

    if body.get("prices") is not None:  # exact single-property match, no second call needed
        return body.get("prices") or [], body.get("name", "")

    properties = body.get("properties") or []
    if not properties:
        return [], ""

    target_words = set(_normalize(hotel_name).split())
    best = properties[0]
    best_name = best.get("name", "")
    overlap = target_words & set(_normalize(best_name).split())
    if len(overlap) < 2:
        return [], best_name

    token = best.get("property_token")
    if not token:
        return [], best_name

    detail = _query(q, check_in, check_out, guests, property_token=token)
    return detail.get("prices") or [], detail.get("name", best_name)


def get_brand_rate_serpapi(
    hotel_name: str,
    address: str,
    brand_name: str,
    brand_domain: str,
    check_in: date,
    check_out: date,
    guests: int = 2,
    brand_url: str = "",
) -> dict:
    """Fetch the brand-direct ("official site") rate for one hotel/date via
    SerpApi's Google Hotels engine. Same return shape as the old
    brand_scraper.get_brand_rate so callers don't need to change.

    The displayed URL is Google's own redirect link for this exact quote
    (`official.get("link")`) rather than the hotel's static overview page —
    it routes through an ad-tracking network (koddi/doubleclick) before
    landing on the brand's real, dated availability page, which looks less
    clean but is the only version that's actually checkable: the URL itself
    carries the same check-in/check-out/guests we asked for (confirmed live
    2026-09-29 by inspecting it), so clicking 🔗 reproduces the exact quote
    instead of dropping onto a generic page with no dates set. `brand_url`
    (the hotel's on-file static link) is kept only as a fallback for the
    rare case Google's response has no link at all.
    """
    try:
        prices, matched_name = _resolve_prices(hotel_name, address, check_in, check_out, guests)
    except Exception as exc:  # noqa: BLE001 - surface any lookup failure in the grid, demo keeps going
        return {"source": "Brand.com", "url": None, "available": False, "lowest_rate": None, "error": str(exc)}

    if not prices:
        note = f" (top match was '{matched_name}')" if matched_name else ""
        return {
            "source": "Brand.com",
            "url": None,
            "available": False,
            "lowest_rate": None,
            "error": f"Couldn't confidently match this hotel in Google Hotels{note}",
        }

    official = next((p for p in prices if p.get("official")), None)
    if not official:
        sources = ", ".join(p.get("source", "?") for p in prices[:5])
        return {
            "source": "Brand.com",
            "url": None,
            "available": False,
            "lowest_rate": None,
            "error": f"Google Hotels has no official {brand_name or 'brand'}-direct listing for this "
            f"property/date ({len(prices)} third-party sources instead: {sources}...)",
        }

    # SerpApi's rate_per_night carries both a tax/fee-inclusive figure
    # (extracted_lowest) and the base nightly rate (extracted_before_taxes_fees).
    # Confirmed live 2026-09-29 against marriott.com directly: extracted_lowest
    # was $620 while the live page showed $529/night — extracted_lowest is the
    # total-with-taxes number, not a wrong or stale quote. Booking.com and
    # Expedia's scrapers here both capture the pre-tax headline rate (what
    # their search grids display; taxes are disclosed separately at
    # checkout), so Brand.com needs the same base to be a fair parity
    # comparison — prefer before_taxes_fees, falling back to the inclusive
    # figure only if SerpApi didn't break it out for this listing.
    per_night = official.get("rate_per_night") or {}
    rate = per_night.get("extracted_before_taxes_fees") or per_night.get("extracted_lowest")
    if not rate or rate <= 0:
        return {
            "source": "Brand.com",
            "url": official.get("link") or brand_url,
            "available": False,
            "lowest_rate": None,
            "error": "Official listing found but no usable rate for this date",
        }

    return {
        "source": "Brand.com",
        "url": official.get("link") or brand_url,
        "available": True,
        "lowest_rate": rate,
        "currency": "USD",
        "room_type": None,
    }
