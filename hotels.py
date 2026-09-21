"""Demo hotel set for the scraping POC — real pilot property + one competitor.

Fairfield Inn & Suites San Francisco Airport/Millbrae is the property named
in RevRadar_Pilot_Capture_and_Parser_Spec.md as the "Slot C" candidate — it's
primary because the client's own PMS "On the Books" report
(BookingStats09.10.26.pdf) is pulled from its Agilysys STAY account, not
because of how it's listed in any compset table. Crowne Plaza San Francisco
International Airport is one competitor from that property's own rate-shop
compset ("Data at a Glance_SFOAB_11092026.xlsx"), trimmed down from the full
7-competitor set for a smaller demo. Addresses and brand-site URLs verified
by web search on 2026-09-21.

`brand_url` + `brand_domain` are used by brand_scraper.py for the
"rate parity" check (the hotel's own official-site rate, alongside
Booking.com/Expedia) — see PLAN.md's original scope note that this is the
hardest scraping problem and the most compelling part of the demo.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Hotel:
    name: str
    is_primary: bool
    address: str  # used for Expedia's address-based search
    booking_url: str = ""  # skips Booking.com search step when known
    brand_name: str = ""
    brand_url: str = ""
    brand_domain: str = ""  # dispatch key for brand_scraper.py's date-param builders


HOTELS: list[Hotel] = [
    Hotel(
        name="Fairfield Inn & Suites San Francisco Airport/Millbrae",
        is_primary=True,
        address="250 El Camino Real, Millbrae, CA 94030",
        brand_name="Marriott",
        brand_url="https://www.marriott.com/en-us/hotels/sfome-fairfield-inn-and-suites-san-francisco-airport-millbrae/overview/",
        brand_domain="marriott.com",
    ),
    Hotel(
        name="Crowne Plaza San Francisco International Airport",
        is_primary=False,
        address="1177 Airport Blvd, Burlingame, CA 94010",
        booking_url="https://www.booking.com/hotel/us/san-fransisco-airport-burlingame.html",
        brand_name="IHG",
        brand_url="https://www.ihg.com/crowneplaza/hotels/us/en/burlingame/urlca/hoteldetail",
        brand_domain="ihg.com",
    ),
]
