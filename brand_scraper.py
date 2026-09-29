"""Brand-direct ("rate parity") scraper — the hotel's own official site.

Was Firecrawl-based (LLM extraction with a hallucination-grounding check) —
see git history for that version. Replaced 2026-09-29 after hitting two dead
ends live: Marriott blocks Firecrawl's render engines outright
(SCRAPE_ALL_ENGINES_FAILED on every attempt), and IHG's booking flow is a
login-gated shell Firecrawl can render but that has no room results ever
loaded — so even a working extraction had nothing real to read.

Now delegates to serpapi_client.get_brand_rate_serpapi (Google Hotels'
"prices" breakdown, sourced from Google's own crawl rather than an LLM
reading a rendered page). This module is kept as a thin wrapper so the
existing call site (`from brand_scraper import get_brand_rate`) and return
shape don't change.
"""

from datetime import date

from serpapi_client import get_brand_rate_serpapi


def get_brand_rate(
    hotel_name: str,
    address: str,
    brand_name: str,
    brand_domain: str,
    check_in: date,
    check_out: date,
    guests: int = 2,
    brand_url: str = "",
) -> dict:
    return get_brand_rate_serpapi(
        hotel_name, address, brand_name, brand_domain, check_in, check_out, guests, brand_url
    )
