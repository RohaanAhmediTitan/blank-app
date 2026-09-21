"""Expedia scraper, ported from pricepoint-backend/src/scrapers/expedia.scraper.ts.

Expedia has no stable direct hotel-page URL to hand it like Booking.com does,
so this mirrors production's address-based search: put the hotel's street
address in `destination` with `sort=DISTANCE`, and the correct hotel comes
back as the first result — no hotel name needed, no browser needed.
"""

from datetime import date
from urllib.parse import urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from firecrawl_client import firecrawl_fetch_html

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}


def _build_search_url(address: str, check_in: date, check_out: date, guests: int = 2, rooms: int = 1) -> str:
    check_in_str = check_in.isoformat()
    check_out_str = check_out.isoformat()
    params = {
        "destination": address,
        "d1": check_in_str,
        "startDate": check_in_str,
        "d2": check_out_str,
        "endDate": check_out_str,
        "adults": str(guests),
        "rooms": str(rooms),
        "sort": "DISTANCE",
        "categorySearch": "hotels_option",
        "useRewards": "false",
    }
    return f"https://www.expedia.com/Hotel-Search?{urlencode(params)}"


def _parse_search_results(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for link in soup.select('a[data-stid="open-product-information"]'):
        href = link.get("href") or ""
        if not href:
            continue
        hidden_span = link.select_one("span.is-visually-hidden")
        name = ""
        if hidden_span:
            name = hidden_span.get_text(strip=True)
            name = name.replace("More information about ", "").split(", opens in a new tab")[0].strip()
        results.append({"name": name or "Hotel", "href": href})
    return results


def _parse_price(text: str) -> tuple[float | None, str]:
    currency = "USD"
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    try:
        return (float(digits) if digits else None), currency
    except ValueError:
        return None, currency


def _parse_detail_rooms(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one('div[data-stid="section-room-list"]')
    if not section:
        return []

    titles = section.select("h3.uitk-heading.uitk-heading-6")
    prices = section.select("div.uitk-text.uitk-type-400.uitk-type-medium.uitk-text-emphasis-theme")

    rooms = []
    for title_el, price_el in zip(titles, prices):
        title = title_el.get_text(strip=True)
        price_text = price_el.get_text(strip=True)  # e.g. "$127 total"
        if not title or not price_text:
            continue
        price, currency = _parse_price(price_text)
        if price:
            rooms.append({"room_type": title, "price": price, "currency": currency})
    return rooms


def resolve_expedia_detail_url(address: str, check_in: date, check_out: date, guests: int = 2) -> str | None:
    """Search once per hotel and return its canonical (query-stripped) detail-page URL.

    The search result is the same hotel regardless of which date we search
    for, so callers should resolve this once and reuse it across every date
    instead of re-searching per date (that redundant search was the single
    biggest cause of slow fetches — 2x the Firecrawl calls for no reason).
    """
    search_url = _build_search_url(address, check_in, check_out, guests)
    search_html = firecrawl_fetch_html(search_url, max_age=172800000)
    hotels = _parse_search_results(search_html)
    if not hotels:
        return None
    href = hotels[0]["href"]
    detail_url = href if href.startswith("http") else f"https://www.expedia.com{href}"
    parsed = urlparse(detail_url)
    return urlunparse(parsed._replace(query=""))


def get_expedia_rate(
    address: str,
    check_in: date,
    check_out: date,
    guests: int = 2,
    detail_url: str | None = None,
) -> dict:
    """Fetch and parse one hotel/date. Pass a pre-resolved `detail_url` (see
    `resolve_expedia_detail_url`) to skip searching again for every date."""
    if not detail_url:
        detail_url = resolve_expedia_detail_url(address, check_in, check_out, guests)
    if not detail_url:
        return {"source": "Expedia", "url": None, "available": False, "lowest_rate": None, "room_type": None}

    parsed = urlparse(detail_url)
    params = {
        "chkin": check_in.isoformat(),
        "chkout": check_out.isoformat(),
        "x_pwa": "1",
        "rfrr": "HSR",
        "adults": str(guests),
    }
    final_url = urlunparse(parsed._replace(query=urlencode(params)))

    # Expedia's room list renders client-side after a skeleton placeholder —
    # without a render wait, Firecrawl captures the page before rates populate.
    detail_html = firecrawl_fetch_html(final_url, wait_for=8000, max_age=172800000)
    rooms = _parse_detail_rooms(detail_html)

    if not rooms:
        return {"source": "Expedia", "url": final_url, "available": False, "lowest_rate": None, "room_type": None}

    cheapest = min(rooms, key=lambda r: r["price"])
    return {
        "source": "Expedia",
        "url": final_url,
        "available": True,
        "lowest_rate": cheapest["price"],
        "currency": cheapest["currency"],
        "room_type": cheapest["room_type"],
    }
