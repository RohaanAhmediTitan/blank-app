"""Booking.com scraper, ported from pricepoint-backend/src/scrapers/booking.scraper.ts.

For the 6 pre-validated demo hotels we already have direct Booking.com URLs,
so `get_booking_rate` skips the production scraper's search-page step (same
shortcut PLAN.md calls out). `search_booking_url` below ports that search
step anyway, for hotels added live during a demo with just a name — mirrors
`scrapeBookingSearchPage`. The detail-page URL construction and room/price
table parsing mirror `buildBookingUrlWithDates` and `scrapeBookingDetailPage`.
"""

import re
from datetime import date
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from bs4 import BeautifulSoup

from firecrawl_client import firecrawl_fetch_html

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}


def search_booking_url(search_text: str, check_in: date, check_out: date, guests: int = 2) -> str | None:
    """Search Booking.com by free-text name and return the first result's hotel-page URL."""
    formatted = "+".join(w.capitalize() for w in search_text.split())
    search_url = (
        f"https://www.booking.com/searchresults.en-gb.html?ss={formatted}"
        f"&group_adults={guests}&no_rooms=1&checkin={check_in.isoformat()}&checkout={check_out.isoformat()}"
    )
    html = firecrawl_fetch_html(search_url, wait_for=3000)
    soup = BeautifulSoup(html, "html.parser")

    for link in soup.select('a[data-testid="title-link"]'):
        name = link.get_text(separator=" ", strip=True)
        name = re.sub(r"Opens in new window", "", name, flags=re.IGNORECASE)
        name = re.sub(r"This property is unavailable.*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+", " ", name).strip()
        href = link.get("href") or ""
        if not href.startswith("http"):
            href = f"https://www.booking.com{href}"
        if name and href:
            parsed = urlparse(href)
            return urlunparse(parsed._replace(query=""))  # drop tracking params, keep canonical page URL
    return None


def build_booking_url(base_url: str, check_in: date, check_out: date, guests: int = 2) -> str:
    parsed = urlparse(base_url)
    params = dict(parse_qsl(parsed.query))
    params.update(
        {
            "checkin": check_in.isoformat(),
            "checkout": check_out.isoformat(),
            "group_adults": str(guests),
            "selected_currency": "USD",
            "no_rooms": "1",
            "group_children": "0",
        }
    )
    return urlunparse(parsed._replace(query=urlencode(params)))


def _parse_price(text: str) -> tuple[float | None, str]:
    currency = "USD"
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break
    digits = "".join(ch for ch in text if ch.isdigit() or ch in ".,")
    digits = digits.replace(",", "")
    try:
        return (float(digits) if digits else None), currency
    except ValueError:
        return None, currency


def parse_booking_detail_html(html: str) -> dict:
    """Returns {"is_sold_out": bool, "rooms": [{"room_type", "price", "currency"}]}."""
    soup = BeautifulSoup(html, "html.parser")

    if soup.select_one("#no_availability_msg"):
        return {"is_sold_out": True, "rooms": []}

    rooms = []
    for row in soup.select("table#hprt-table > tbody > tr"):
        th = row.find("th")
        room_type = ""
        if th:
            name_el = th.select_one("span.hprt-roomtype-icon-link")
            if name_el:
                room_type = name_el.get_text(strip=True)

        if not room_type:
            continue

        # Booking.com's markup has shifted over time (the original td[1]-based
        # index the production scraper used no longer lines up), so prefer the
        # dedicated price cell/span by class, with a positional fallback.
        price_cell = row.select_one("td.hprt-table-cell-price") or (
            row.find_all("td")[1] if len(row.find_all("td")) > 1 else None
        )
        if not price_cell:
            continue

        price_span = price_cell.select_one("span.js-average-per-night-price") or price_cell.find("span")
        if not price_span:
            continue

        raw_price = price_span.get("data-price-per-night-raw")
        if raw_price:
            try:
                price, currency = float(raw_price), "USD"
            except ValueError:
                price, currency = _parse_price(price_span.get_text(strip=True))
        else:
            price_text = price_span.get_text(strip=True)
            if "show prices" in price_text.lower():
                continue
            price, currency = _parse_price(price_text)

        if price:
            rooms.append({"room_type": room_type, "price": price, "currency": currency})

    return {"is_sold_out": False, "rooms": rooms}


def get_booking_rate(base_url: str, check_in: date, check_out: date, guests: int = 2) -> dict:
    """Fetch and parse one hotel/date. Returns lowest-priced room, or None if sold out/unavailable."""
    url = build_booking_url(base_url, check_in, check_out, guests)
    html = firecrawl_fetch_html(url, wait_for=5000)
    parsed = parse_booking_detail_html(html)

    if parsed["is_sold_out"] or not parsed["rooms"]:
        return {"source": "Booking.com", "url": url, "available": False, "lowest_rate": None, "room_type": None}

    cheapest = min(parsed["rooms"], key=lambda r: r["price"])
    return {
        "source": "Booking.com",
        "url": url,
        "available": True,
        "lowest_rate": cheapest["price"],
        "currency": cheapest["currency"],
        "room_type": cheapest["room_type"],
    }
