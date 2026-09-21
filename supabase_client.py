"""Supabase persistence for the RevRadar rate-shop demo.

Two tables (see sql/schema.sql):
  - client_hotels: hotels the client adds through the app's "Add a hotel"
    form, persisted so they survive app restarts and are visible to anyone
    who opens the deployed link — not just one browser's session state.
  - rate_shops: every fetched row, appended (never overwritten), so rate
    history persists across fetches and across sessions.

Supabase is optional — if SUPABASE_URL/SUPABASE_KEY aren't set, every
function here is a no-op and the app falls back to session-only state
(see streamlit_app.py), so the POC still runs without a Supabase project.
"""

import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

_enabled = bool(SUPABASE_URL and SUPABASE_KEY)
_client = None

if _enabled:
    from supabase import create_client

    _client = create_client(SUPABASE_URL, SUPABASE_KEY)


def is_enabled() -> bool:
    return _enabled


def load_client_hotels() -> list[dict]:
    """All client-added hotels, oldest first. Empty list if Supabase isn't configured."""
    if not _enabled:
        return []
    resp = _client.table("client_hotels").select("*").order("created_at").execute()
    return resp.data or []


def save_client_hotel(
    name: str,
    address: str,
    booking_url: str = "",
    brand_name: str = "",
    brand_url: str = "",
    brand_domain: str = "",
) -> None:
    if not _enabled:
        return
    _client.table("client_hotels").insert(
        {
            "name": name,
            "address": address or None,
            "booking_url": booking_url or None,
            "brand_name": brand_name or None,
            "brand_url": brand_url or None,
            "brand_domain": brand_domain or None,
        }
    ).execute()


def save_rate_shop_rows(rows: list[dict]) -> None:
    """Persist one fetch's results. `rows` are the same dicts rendered in the grid."""
    if not _enabled or not rows:
        return
    payload = []
    for r in rows:
        check_in = r.get("Date")
        payload.append(
            {
                "hotel_name": r.get("Hotel"),
                "source": r.get("source"),
                "check_in": check_in.isoformat() if isinstance(check_in, date) else check_in,
                "available": bool(r.get("available")),
                "lowest_rate": r.get("lowest_rate"),
                "currency": r.get("currency"),
                "room_type": r.get("room_type"),
                "url": r.get("url"),
                "error": r.get("error"),
            }
        )
    _client.table("rate_shops").insert(payload).execute()
