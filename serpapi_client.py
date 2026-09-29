"""SerpApi Google Hotels client — sole rate source for every third-party
source in THIRD_PARTY_SOURCES (Booking.com, Expedia, Agoda, Priceline,
Hotels.com, trivago) plus Brand.com.

Started as brand.com-only (see git history), which hit two Firecrawl dead
ends: Marriott blocks its render engines outright (SCRAPE_ALL_ENGINES_FAILED
on every attempt), and IHG's booking flow is a login-gated shell with no room
results to read. SerpApi returns Google's own structured price data instead
of an LLM reading a rendered page, so there's no hallucination risk, and
Google's crawl reaches sites Firecrawl's engines can't.

Booking.com and Expedia moved here too (2026-09-29) after direct Firecrawl
scraping of Booking.com was caught returning a different, lower price than
what a real browser saw for the identical hotel/date/room — confirmed live,
not a parsing bug (the correct room row was being read; the served content
itself differed, most likely Booking.com treating the automated/datacenter
request differently). SerpApi's figure for the same hotel/date matched the
real page exactly. No fallback to the old Firecrawl-based scrapers: if
Google's feed doesn't have a source for a given property/date, that source
reports unavailable rather than falling back to a mechanism just shown to be
untrustworthy.

Response shape, confirmed live (2026-09-29):
  - A specific enough query (name + full address) sometimes gets Google to
    resolve straight to one property, returning its full detail payload —
    including a top-level `prices` array — in a single call.
  - A less specific query instead returns a `properties` list (multiple
    candidates), each with only a `property_token`; getting `prices` then
    needs a second call with that token (and the same `q`, which SerpApi
    requires even for a token-based lookup).
  - Each entry in `prices` covers one source (an OTA, a deals aggregator, or
    the brand's own site) with a `rate_per_night`. The brand's own listing is
    marked `"official": true` — far more reliable than matching brand
    name/domain against `source`, since `source` for the official entry is
    just the property's own display name (e.g. "Fairfield by Marriott Inn &
    Suites..."), not a domain or brand string. Booking.com/Expedia entries
    are matched by a plain substring on `source` instead (seen as both
    "Expedia.com" and "Expedia.co.uk" across properties).
  - `rate_per_night` carries both a tax/fee-inclusive figure
    (extracted_lowest) and the base nightly rate
    (extracted_before_taxes_fees). Confirmed live against marriott.com
    directly: extracted_lowest read $620 while the live page showed
    $529/night before tax — extracted_lowest is the inclusive total, not a
    wrong number. Every source here uses before_taxes_fees (falling back to
    the inclusive figure only if a listing doesn't break it out), matching
    the public/Best Available Rate convention industry rate-shopping tools
    use for parity comparisons.

One call per hotel/date returns every source at once (they're all in the
same `prices` array), so callers should fetch once via `get_all_source_rates`
rather than querying per source.

Coverage is inherently partial: a miss here means Google's hotel-price feed
doesn't have a listing for this source/property/date, not necessarily that
no rate exists.
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


def _find_by_keyword(prices: list[dict], keyword: str) -> dict | None:
    kw = keyword.lower()
    return next((p for p in prices if kw in (p.get("source") or "").lower()), None)


def _rate_from_entry(entry: dict) -> float | None:
    per_night = entry.get("rate_per_night") or {}
    return per_night.get("extracted_before_taxes_fees") or per_night.get("extracted_lowest")


# Third-party sources matched by a plain substring on Google's `source` field
# (confirmed live: labels vary a bit, e.g. "Expedia.com" vs "Expedia.co.uk",
# so a substring is more robust than an exact match). This is a curated
# subset of the 20-30+ sources Google Hotels typically returns per property —
# limited to major, recognizable OTAs rather than every reseller/aggregator
# in the feed (many of those are small meta-search resellers with no brand
# recognition, and the full list varies wildly in length between properties,
# which would make the grid inconsistent from one hotel to the next).
THIRD_PARTY_SOURCES: list[tuple[str, str]] = [
    ("Booking.com", "booking"),
    ("Expedia", "expedia"),
    ("Agoda", "agoda"),
    ("Priceline", "priceline"),
    ("Hotels.com", "hotels.com"),
    ("trivago", "trivago"),
]

ALL_SOURCE_NAMES: list[str] = [name for name, _ in THIRD_PARTY_SOURCES] + ["Brand.com"]


def get_all_source_rates(
    hotel_name: str,
    address: str,
    check_in: date,
    check_out: date,
    guests: int = 2,
    brand_name: str = "",
    booking_url: str = "",
    expedia_url: str = "",
    brand_url: str = "",
) -> dict[str, dict]:
    """One SerpApi lookup per hotel/date, returning every source in
    THIRD_PARTY_SOURCES plus Brand.com all at once — Google's Hotels feed
    already lists every source for a property in the same response, so
    there's no reason to call the API once per source for data that comes
    back in one call.

    The displayed URL is Google's own link for that quote where available
    (for Brand.com, confirmed live, it's a dated redirect that reproduces
    the exact quote rather than a generic overview page with no dates set);
    `booking_url`/`expedia_url`/`brand_url` are used only as a fallback for
    the three sources that have an on-file static link, when Google's entry
    has none.
    """
    try:
        prices, matched_name = _resolve_prices(hotel_name, address, check_in, check_out, guests)
    except Exception as exc:  # noqa: BLE001 - surface any lookup failure in the grid, demo keeps going
        error = str(exc)
        return {
            key: {"source": key, "url": None, "available": False, "lowest_rate": None, "error": error}
            for key in ALL_SOURCE_NAMES
        }

    if not prices:
        note = f" (top match was '{matched_name}')" if matched_name else ""
        error = f"Couldn't confidently match this hotel in Google Hotels{note}"
        return {
            key: {"source": key, "url": None, "available": False, "lowest_rate": None, "error": error}
            for key in ALL_SOURCE_NAMES
        }

    fallback_urls = {"Booking.com": booking_url, "Expedia": expedia_url}
    official_entry = next((p for p in prices if p.get("official")), None)

    lookups: list[tuple[str, dict | None, str, str]] = [
        (name, _find_by_keyword(prices, keyword), fallback_urls.get(name, ""), name)
        for name, keyword in THIRD_PARTY_SOURCES
    ]
    lookups.append(("Brand.com", official_entry, brand_url, f"official {brand_name or 'brand'}-direct"))

    results: dict[str, dict] = {}
    for key, entry, fallback_url, missing_label in lookups:
        if not entry:
            sources = ", ".join(p.get("source", "?") for p in prices[:5])
            results[key] = {
                "source": key,
                "url": None,
                "available": False,
                "lowest_rate": None,
                "error": f"Google Hotels has no {missing_label} listing for this property/date "
                f"({len(prices)} sources found instead: {sources}...)",
            }
            continue

        url = entry.get("link") or fallback_url
        rate = _rate_from_entry(entry)
        if not rate or rate <= 0:
            results[key] = {
                "source": key,
                "url": url,
                "available": False,
                "lowest_rate": None,
                "error": "Listing found but no usable rate for this date",
            }
            continue

        results[key] = {
            "source": key,
            "url": url,
            "available": True,
            "lowest_rate": rate,
            "currency": "USD",
            "room_type": None,
        }

    return results
