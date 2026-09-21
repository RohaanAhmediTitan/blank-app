"""Brand-direct ("rate parity") scraper — the hotel's own official site.

OTA scrapers (booking_scraper.py, expedia_scraper.py) parse known HTML
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
expected to be as reliable as the OTA scrapers.
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
            "source": "Brand direct",
            "url": None,
            "available": False,
            "lowest_rate": None,
            "error": "No brand-direct URL on file for this hotel",
        }

    url = _dated_url(brand_url, brand_domain, check_in, check_out, guests)
    prompt = (
        f"This is a hotel's official booking page. Find the lowest nightly room rate for "
        f"{guests} guest(s), 1 room, check-in {check_in.isoformat()}, check-out {check_out.isoformat()}. "
        "If the live availability search doesn't reflect those exact dates (e.g. the widget defaulted to "
        "today/tomorrow, or only a marketing 'rates from $X' figure is shown), report that rate anyway and "
        "note which dates it actually applies to in dates_shown. Only set available to false if no rate "
        "figure is visible on the page at all."
    )
    try:
        data = firecrawl_extract([url], RATE_SCHEMA, prompt)
    except Exception as exc:  # noqa: BLE001 - surface any extract failure in the grid, demo keeps going
        return {"source": "Brand direct", "url": url, "available": False, "lowest_rate": None, "error": str(exc)}

    if not data or not data.get("available"):
        return {
            "source": "Brand direct",
            "url": url,
            "available": False,
            "lowest_rate": None,
            "room_type": data.get("room_type") if data else None,
            "error": None if data else "Extract job failed or timed out",
        }

    return {
        "source": "Brand direct",
        "url": url,
        "available": True,
        "lowest_rate": data.get("lowest_rate"),
        "currency": "USD",
        "room_type": data.get("room_type"),
        "dates_shown": data.get("dates_shown"),
    }
