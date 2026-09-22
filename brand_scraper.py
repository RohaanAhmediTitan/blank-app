"""Brand-direct ("rate parity") scraper — the hotel's own official site.

Third-party scrapers (booking_scraper.py, expedia_scraper.py) parse known HTML
structure for one site each. Brand-direct doesn't offer that here: this demo
set alone spans 5 different booking engines (Marriott, Hilton, IHG, Wyndham,
Best Western), each with different markup, and some ignore date query
params we can't verify without a live account. So this uses Firecrawl's
/v2/extract (LLM-based reading of the rendered page) instead of a
hand-written parser per brand — the same approach PLAN.md flagged as the
hardest, most demo-worthy part of the POC.

Best-effort by design: we try a date-stamped URL where the brand's query
param pattern is reasonably well known, but if the site ignores it or shows
its own default dates, the extraction prompt asks the model to report
whatever it actually saw (and which dates that reflects) rather than fail
silently. This is the "Own rate not found" / parity-check side of the
Alert Catalog's Rate Integrity and Parity & Distribution sections — not
expected to be as reliable as the third-party-site scrapers.

Confirmed live (2026-09-22) that the model will confidently fabricate a
plausible room rate on a page with zero pricing content — reproduced on
both Marriott's overview page (fully blocked by Firecrawl's engines, so
there's no page content at all) and IHG's booking flow (a login/session-gate
shell with no room results ever loaded). So every extraction now has to cite
the exact page text it read the rate from, and that citation is verified
against the actual scraped markdown before the rate is trusted — see
get_brand_rate's `grounded` check. An extraction that fails this is treated
as unavailable, not as a real (if unreliable) rate.
"""

from datetime import date
from urllib.parse import urlencode

from firecrawl_client import firecrawl_extract

RATE_SCHEMA = {
    "type": "object",
    "properties": {
        "available": {
            "type": "boolean",
            "description": "True if a nightly room rate is shown anywhere on the page.",
        },
        "lowest_rate": {
            "type": ["number", "null"],
            "description": "Lowest nightly rate shown, in dollars, before taxes/fees if those are broken out separately.",
        },
        "room_type": {"type": ["string", "null"], "description": "Name of the room type at that rate."},
        "dates_shown": {
            "type": ["string", "null"],
            "description": "The check-in/check-out dates the displayed rate actually applies to, as shown on "
            "the page (may differ from the dates requested if the booking widget ignored the URL params).",
        },
        "evidence": {
            "type": ["string", "null"],
            "description": "Copy the exact line of text from the page that shows the rate (verbatim, including "
            "the dollar figure). Required whenever available is true — a rate with no matching text on the page "
            "cannot be verified and will be discarded.",
        },
    },
    "required": ["available"],
}

# Best-effort date query-param patterns per booking-engine domain, built from
# each brand's publicly observable URL shape. Unverified against a live
# account — treat as a first attempt, not a guarantee; the extract prompt is
# written to degrade gracefully (report the default-dates rate) if a site
# ignores these.
_DATE_PARAM_BUILDERS = {
    "marriott.com": lambda ci, co, guests: {
        "checkInDate": ci,
        "checkOutDate": co,
        "numRooms": "1",
        "numGuests": str(guests),
    },
    "hilton.com": lambda ci, co, guests: {
        "arrivalDate": ci,
        "departureDate": co,
        "numberOfAdults[0]": str(guests),
    },
    "wyndhamhotels.com": lambda ci, co, guests: {
        "checkInDate": ci,
        "checkOutDate": co,
        "rooms": "1",
        "adults": str(guests),
    },
    "ihg.com": lambda ci, co, guests: {
        "fromDate": ci,
        "toDate": co,
        "adults": str(guests),
        "rooms": "1",
    },
    "bestwestern.com": lambda ci, co, guests: {
        "checkIn": ci,
        "checkOut": co,
        "rooms": "1",
        "adults": str(guests),
    },
}


def _dated_url(brand_url: str, brand_domain: str, check_in: date, check_out: date, guests: int) -> str:
    builder = _DATE_PARAM_BUILDERS.get(brand_domain)
    if not builder:
        return brand_url
    sep = "&" if "?" in brand_url else "?"
    return brand_url + sep + urlencode(builder(check_in.isoformat(), check_out.isoformat(), guests))


# Confirmed live (2026-09-21/22), repeatedly: Firecrawl's scraping engines
# cannot reach marriott.com at all — even a plain content fetch with no
# extraction 500s with SCRAPE_ALL_ENGINES_FAILED. Since that's now also
# non-retried at the client level (see firecrawl_client._NON_RETRYABLE_CODES),
# this domain would still cost one wasted credit per fetch forever; skipping
# it outright costs nothing instead. Revisit if Firecrawl's engines change —
# this isn't a permanent architectural limit, just today's observed reality.
_KNOWN_BLOCKED_DOMAINS = {"marriott.com"}


def get_brand_rate(
    brand_url: str,
    brand_domain: str,
    check_in: date,
    check_out: date,
    guests: int = 2,
) -> dict:
    """Fetch the brand-direct rate for one hotel/date via Firecrawl extract."""
    if not brand_url:
        return {
            "source": "Brand.com",
            "url": None,
            "available": False,
            "lowest_rate": None,
            "error": "No brand-direct URL on file for this hotel",
        }

    if brand_domain in _KNOWN_BLOCKED_DOMAINS:
        return {
            "source": "Brand.com",
            "url": brand_url,
            "available": False,
            "lowest_rate": None,
            "error": f"{brand_domain} confirmed to block Firecrawl outright — skipped to avoid a wasted credit",
        }

    url = _dated_url(brand_url, brand_domain, check_in, check_out, guests)
    prompt = (
        f"This is a hotel's official booking page. Find the lowest nightly room rate for "
        f"{guests} guest(s), 1 room, check-in {check_in.isoformat()}, check-out {check_out.isoformat()}. "
        "If the live availability search doesn't reflect those exact dates (e.g. the widget defaulted to "
        "today/tomorrow, or only a marketing 'rates from $X' figure is shown), report that rate anyway and "
        "note which dates it actually applies to in dates_shown. Only set available to false if no rate "
        "figure is visible on the page at all. A room does not cost $0 — if the only number you can find is "
        "$0 or blank (e.g. a 'due at hotel' or deposit line, not the actual room rate), treat that as no rate "
        "found and set available to false rather than reporting 0. Do not guess or estimate a plausible-sounding "
        "rate if you cannot actually find one on the page — many pages here are login walls, marketing pages, or "
        "session-expired shells with no pricing at all, and reporting a number in that case is worse than saying "
        "unavailable. Set evidence to the exact page text the rate came from; if you can't quote real text "
        "containing the number, you don't actually have a rate — set available to false."
    )
    try:
        data, page_markdown = firecrawl_extract(url, RATE_SCHEMA, prompt)
    except Exception as exc:  # noqa: BLE001 - surface any extract failure in the grid, demo keeps going
        return {"source": "Brand.com", "url": url, "available": False, "lowest_rate": None, "error": str(exc)}

    rate = data.get("lowest_rate") if data else None
    evidence = (data.get("evidence") or "").strip() if data else ""
    # Grounding check: live-tested proof the model will otherwise hallucinate a
    # plausible-looking rate on a page with zero pricing content (confirmed on
    # both a marketing overview page and a login-gated booking page). Requiring
    # a verbatim quote and confirming it actually appears in the scraped page
    # text turns that failure mode from "a confident but fake number" into a
    # clean "couldn't verify," which is what it actually is.
    grounded = bool(evidence) and _normalize(evidence) in _normalize(page_markdown)
    if not data or not data.get("available") or not rate or rate <= 0 or not grounded:
        error = None
        if data and data.get("available") and rate and not grounded:
            error = "Extraction wasn't grounded in the page's actual text (likely hallucinated) — discarded"
        elif not data:
            error = "Firecrawl returned no data for this page"
        return {
            "source": "Brand.com",
            "url": url,
            "available": False,
            "lowest_rate": None,
            "room_type": data.get("room_type") if data else None,
            "error": error,
        }

    return {
        "source": "Brand.com",
        "url": url,
        "available": True,
        "lowest_rate": rate,
        "currency": "USD",
        "room_type": data.get("room_type"),
        "dates_shown": data.get("dates_shown"),
    }


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()
