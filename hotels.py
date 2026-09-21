"""Demo hotel set for the scraping POC — real pilot property + compset.

Fairfield Inn & Suites San Francisco Airport/Millbrae is the property named
in RevRadar_Pilot_Capture_and_Parser_Spec.md as the "Slot C" candidate, and
the competitor set below is the real compset from the client's own
"Data at a Glance_SFOAB_11092026.xlsx" rate-analysis sheet — not a
placeholder. Addresses and brand-site URLs verified by web search on
2026-09-21.

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
        name="Best Western Grosvenor Airport South San Francisco",
        is_primary=False,
        address="380 S Airport Blvd, South San Francisco, CA 94080",
        brand_name="Best Western",
        brand_url="https://www.bestwestern.com/en_US/book/hotels-in-south-san-francisco/best-western-plus-grosvenor-airport-hotel/propertyCode.05297.html",
        brand_domain="bestwestern.com",
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
    Hotel(
        name="Hampton Inn & Suites Burlingame",
        is_primary=False,
        address="1755 Bayshore Hwy, Burlingame, CA 94010",
        booking_url="https://www.booking.com/hotel/us/hampton-inn-suites-san-francisco-burlingame-airport-south.html",
        brand_name="Hilton",
        brand_url="https://www.hilton.com/en/hotels/sfohshx-hampton-suites-san-francisco-burlingame-airport-south/",
        brand_domain="hilton.com",
    ),
    Hotel(
        name="Hampton Inn San Francisco Airport",
        is_primary=False,
        address="300 Gateway Blvd, South San Francisco, CA 94080",
        booking_url="https://www.booking.com/hotel/us/hampton-inn-san-francisco-airport.html",
        brand_name="Hilton",
        brand_url="https://www.hilton.com/en/hotels/sfohhhx-hampton-san-francisco-airport/",
        brand_domain="hilton.com",
    ),
    Hotel(
        name="Holiday Inn Express San Francisco Airport South",
        is_primary=False,
        address="1250 Old Bayshore Hwy, Burlingame, CA 94010",
        booking_url="https://www.booking.com/hotel/us/holiday-inn-express-san-francisco-airport-south.html",
        brand_name="IHG",
        brand_url="https://www.ihg.com/holidayinnexpress/hotels/us/en/burlingame/urlbh/hoteldetail",
        brand_domain="ihg.com",
    ),
    Hotel(
        name="La Quinta By Wyndham San Francisco Airport North",
        is_primary=False,
        address="20 Airport Blvd, South San Francisco, CA 94080",
        booking_url="https://www.booking.com/hotel/us/la-quinta-inn-san-francisco-airport-north.html",
        brand_name="Wyndham",
        brand_url="https://www.wyndhamhotels.com/laquinta/s-san-francisco-california/la-quinta-san-francisco-airport-north/overview",
        brand_domain="wyndhamhotels.com",
    ),
    Hotel(
        name="Travelodge San Francisco Airport North",
        is_primary=False,
        address="326 S Airport Blvd, South San Francisco, CA 94080",
        brand_name="Wyndham",
        brand_url="https://www.wyndhamhotels.com/travelodge/south-san-francisco-california/travelodge-san-francisco-airport-north/overview",
        brand_domain="wyndhamhotels.com",
    ),
]
