"""RevRadar scraping POC — live rate comparison grid, now client-facing.

Fetches live Booking.com + Expedia + brand-direct rates for a primary hotel
and its competitors over a short date window, renders a hotel x date grid
with the cheapest rate per night highlighted, plus a rate-parity view
(brand-direct vs. cheapest third-party site). Persists added hotels and every fetch's
results to Supabase when configured (see supabase_client.py) — see
docs/poc-scraping-demo/PLAN.md and DEPLOY.md for the full scope history.

Also supports adding an ad-hoc hotel (e.g. one the client names live) beside
the pre-validated set, and surfaces proof-of-liveness (per-row fetch
timestamps + clickable source links) so results are verifiable, not canned.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from booking_scraper import get_booking_rate, search_booking_url
from brand_scraper import get_brand_rate
from expedia_scraper import get_expedia_rate, resolve_expedia_detail_url
from hotels import HOTELS, Hotel
import supabase_client

MAX_WORKERS = 6

# Measured against the live sites (see docs/SESSION_CONTEXT.md): concurrency
# doesn't help — Firecrawl appears to serialize requests per account/plan, so
# these are real per-call wall-clock costs, not something threading hides.
SECONDS_PER_BOOKING_CALL = 14
SECONDS_PER_EXPEDIA_RESOLVE = 8  # once per hotel, not per date
SECONDS_PER_EXPEDIA_DETAIL_CALL = 18
SECONDS_PER_BRAND_CALL = 25  # extract is async (submit + poll), slower than a plain scrape

st.set_page_config(page_title="RevRadar Rate Shop POC", layout="wide")
st.title("RevRadar — Live Rate Comparison POC")
st.caption(
    "Primary: **Fairfield Inn & Suites San Francisco Airport/Millbrae** vs. its real SFO-airport compset. "
    "Rates are fetched live from Booking.com, Expedia, and each hotel's own brand-direct site — not canned data."
)
if supabase_client.is_enabled():
    st.caption("📌 Persistence: connected to Supabase — added hotels and every fetch are saved.")
else:
    st.caption(
        "⚠️ Persistence: SUPABASE_URL/SUPABASE_KEY not set — added hotels and fetch history are "
        "session-only and will be lost on refresh. See sql/schema.sql to enable persistence."
    )

if "custom_hotels" not in st.session_state:
    # Seed from Supabase (if configured) so hotels the client added in a
    # previous session — or that someone else added to this shared link —
    # show up here too, not just in the browser that added them.
    st.session_state["custom_hotels"] = [
        Hotel(
            name=h["name"],
            is_primary=False,
            address=h.get("address") or h["name"],
            booking_url=h.get("booking_url") or "",
            brand_name=h.get("brand_name") or "",
            brand_url=h.get("brand_url") or "",
            brand_domain=h.get("brand_domain") or "",
        )
        for h in supabase_client.load_client_hotels()
    ]

with st.sidebar:
    st.header("Settings")
    date_col1, date_col2 = st.columns(2)
    with date_col1:
        start_date = st.date_input("From", value=date.today() + timedelta(days=1))
    with date_col2:
        end_date = st.date_input("To", value=date.today() + timedelta(days=4))

    num_nights = (end_date - start_date).days
    if num_nights <= 0:
        st.error("**To** must be after **From**.")
        num_nights = 0
    elif num_nights > 30:
        st.warning(f"{num_nights} nights selected — that's a lot of Firecrawl calls per hotel/source. Consider narrowing the range.")

    st.caption(f"{num_nights} night(s)" if num_nights > 0 else "Pick a valid date range to enable fetching.")
    guests = st.number_input("Guests", min_value=1, max_value=4, value=2)
    sources = st.multiselect(
        "Sources",
        ["Booking.com", "Expedia", "Brand.com"],
        default=["Booking.com"],
        help="Expedia needs an 8s render wait to avoid its loading skeleton. Brand.com (rate parity — "
        "the hotel's own official-site rate) is the slowest and least reliable, since it reads whatever "
        "each brand's booking widget renders rather than a known page structure — add it last.",
    )

    st.divider()
    st.subheader("Add a hotel (e.g. one the client names live)")
    with st.expander("📖 How to add a test hotel (guide, with real examples)"):
        guide_path = Path(__file__).parent / "ADDING_A_HOTEL.md"
        st.markdown(guide_path.read_text(encoding="utf-8"))
    with st.form("add_hotel_form", clear_on_submit=True):
        new_name = st.text_input("Hotel name")
        new_city = st.text_input("City, State (used to search Booking.com)")
        new_booking_url = st.text_input("Booking.com URL (optional — skips search if given)")
        new_address = st.text_input("Street address (optional — improves Expedia match)")
        new_brand_name = st.text_input("Brand (optional, e.g. Hilton, Marriott)")
        new_brand_url = st.text_input("Brand-direct booking page URL (optional — enables rate parity check)")
        add_clicked = st.form_submit_button("Add to comparison")
        if add_clicked and new_name:
            brand_domain = urlparse(new_brand_url).netloc.removeprefix("www.") if new_brand_url else ""
            resolved_address = new_address.strip() or f"{new_name}, {new_city}".strip(", ")
            st.session_state["custom_hotels"].append(
                Hotel(
                    name=new_name,
                    is_primary=False,
                    booking_url=new_booking_url.strip(),
                    address=resolved_address,
                    brand_name=new_brand_name.strip(),
                    brand_url=new_brand_url.strip(),
                    brand_domain=brand_domain,
                )
            )
            supabase_client.save_client_hotel(
                name=new_name,
                address=resolved_address,
                booking_url=new_booking_url.strip(),
                brand_name=new_brand_name.strip(),
                brand_url=new_brand_url.strip(),
                brand_domain=brand_domain,
            )
            st.success(f"Added {new_name} — will be included in the next fetch.")

    if st.session_state["custom_hotels"]:
        st.caption("Ad-hoc hotels added this session:")
        for h in st.session_state["custom_hotels"]:
            st.caption(f"• {h.name}")

    all_hotels_preview: list[Hotel] = HOTELS + st.session_state["custom_hotels"]
    n_hotels = len(all_hotels_preview)
    est_seconds = 0.0
    if "Booking.com" in sources:
        est_seconds += n_hotels * num_nights * SECONDS_PER_BOOKING_CALL
    if "Expedia" in sources:
        est_seconds += n_hotels * SECONDS_PER_EXPEDIA_RESOLVE  # once per hotel
        est_seconds += n_hotels * num_nights * SECONDS_PER_EXPEDIA_DETAIL_CALL
    if "Brand.com" in sources:
        est_seconds += n_hotels * num_nights * SECONDS_PER_BRAND_CALL
    # No concurrency discount: measured testing showed Firecrawl mostly
    # serializes our calls on their end regardless of our thread pool size,
    # so plan around this worst-case serial estimate, not an optimistic one.
    est_minutes = est_seconds / 60
    st.caption(
        f"Estimated fetch time for {n_hotels} hotels × {num_nights} night(s): "
        f"**~{est_minutes:.1f} min** (measured per-call times; may occasionally run a bit faster, "
        f"rarely slower — see docs/SESSION_CONTEXT.md)."
    )

    fetch_clicked = st.button("Fetch Rates", type="primary", disabled=num_nights <= 0)

all_hotels: list[Hotel] = HOTELS + st.session_state["custom_hotels"]

st.subheader("POC hotel set")
st.table(
    pd.DataFrame(
        [{"Hotel": h.name, "Role": "Primary" if h.is_primary else "Competitor"} for h in all_hotels]
    )
)

if fetch_clicked:
    dates = [start_date + timedelta(days=i) for i in range(num_nights)]

    # --- Resolve phase: figure out each hotel's URL/detail-page ONCE, not per
    # date. Re-resolving Expedia's hotel search on every single date was the
    # single biggest source of wasted time (2x the calls for no benefit).
    resolve_status = st.empty()
    booking_urls: dict[str, str] = {}
    expedia_urls: dict[str, str | None] = {}
    for hotel in all_hotels:
        booking_url = hotel.booking_url
        if "Booking.com" in sources and not booking_url:
            resolve_status.text(f"Resolving Booking.com match for {hotel.name}...")
            booking_url = search_booking_url(hotel.name, dates[0], dates[0] + timedelta(days=1), guests) or ""
        booking_urls[hotel.name] = booking_url

        if "Expedia" in sources:
            resolve_status.text(f"Resolving Expedia match for {hotel.name}...")
            expedia_urls[hotel.name] = resolve_expedia_detail_url(hotel.address, dates[0], dates[0] + timedelta(days=1), guests)
    resolve_status.empty()

    # --- Fetch phase: every (hotel, date, source) combo is independent once
    # resolved above, so run them concurrently instead of one at a time.
    def _fetch_booking(hotel: Hotel, check_in: date) -> dict:
        check_out = check_in + timedelta(days=1)
        booking_url = booking_urls[hotel.name]
        if booking_url:
            try:
                result = get_booking_rate(booking_url, check_in, check_out, guests)
            except Exception as exc:  # noqa: BLE001 - surface any scrape failure in the grid, demo keeps going
                result = {"source": "Booking.com", "url": booking_url, "available": False, "lowest_rate": None, "error": str(exc)}
        else:
            result = {"source": "Booking.com", "url": None, "available": False, "lowest_rate": None, "error": "No Booking.com match found"}
        return {"Hotel": hotel.name, "Date": check_in, "Fetched at": datetime.now().strftime("%H:%M:%S"), **result}

    def _fetch_expedia(hotel: Hotel, check_in: date) -> dict:
        check_out = check_in + timedelta(days=1)
        try:
            result = get_expedia_rate(hotel.address, check_in, check_out, guests, detail_url=expedia_urls.get(hotel.name))
        except Exception as exc:  # noqa: BLE001
            result = {"source": "Expedia", "url": None, "available": False, "lowest_rate": None, "error": str(exc)}
        return {"Hotel": hotel.name, "Date": check_in, "Fetched at": datetime.now().strftime("%H:%M:%S"), **result}

    def _fetch_brand(hotel: Hotel, check_in: date) -> dict:
        check_out = check_in + timedelta(days=1)
        try:
            result = get_brand_rate(hotel.brand_url, hotel.brand_domain, check_in, check_out, guests)
        except Exception as exc:  # noqa: BLE001
            result = {"source": "Brand.com", "url": hotel.brand_url or None, "available": False, "lowest_rate": None, "error": str(exc)}
        return {"Hotel": hotel.name, "Date": check_in, "Fetched at": datetime.now().strftime("%H:%M:%S"), **result}

    jobs = []
    for hotel in all_hotels:
        for check_in in dates:
            if "Booking.com" in sources:
                jobs.append((_fetch_booking, hotel, check_in))
            if "Expedia" in sources:
                jobs.append((_fetch_expedia, hotel, check_in))
            if "Brand.com" in sources:
                jobs.append((_fetch_brand, hotel, check_in))

    rows = []
    total_calls = len(jobs)
    progress = st.progress(0.0, text="Starting fetch...")
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fn, hotel, check_in): (hotel, check_in) for fn, hotel, check_in in jobs}
        for future in as_completed(futures):
            hotel, check_in = futures[future]
            rows.append(future.result())
            done += 1
            progress.progress(done / max(total_calls, 1), text=f"Fetched {done}/{total_calls} — last: {hotel.name} — {check_in}")

    progress.empty()
    st.session_state["rate_rows"] = rows
    st.session_state["last_fetch_completed_at"] = datetime.now().strftime("%H:%M:%S")
    supabase_client.save_rate_shop_rows(rows)

if "rate_rows" in st.session_state:
    df = pd.DataFrame(st.session_state["rate_rows"])

    st.success(
        f"Last full fetch completed at **{st.session_state['last_fetch_completed_at']}** — "
        f"{len(df)} live requests made just now. Re-click **Fetch Rates** any time to prove it isn't cached."
    )

    st.subheader("Rates by hotel and night")
    st.caption(
        "One row per hotel/night. Each rate is a clickable link straight to the exact page our scraper "
        "read it from — click 🔗 to verify against the live site yourself. Cheapest rate in each row is highlighted."
    )

    SOURCE_ORDER = ["Booking.com", "Expedia", "Brand.com"]
    present_sources = [s for s in SOURCE_ORDER if s in df["source"].unique()]

    def _rate_cell(sub: pd.DataFrame) -> tuple[str, float | None]:
        """Returns (html for this cell, numeric rate for cheapest-highlighting)."""
        if sub.empty:
            return "—", None
        row = sub.iloc[0]
        if not row.get("available") or pd.isna(row.get("lowest_rate")):
            error = row.get("error")
            label = "Couldn't fetch ⚠️" if error else "Sold out"
            title = f' title="{error}"' if error else ""
            return f'<span{title} style="color:#999;">{label}</span>', None
        rate = row["lowest_rate"]
        url = row.get("url")
        text = f"${rate:,.0f}"
        if url:
            return f'<a href="{url}" target="_blank" rel="noopener" style="text-decoration:none;">{text} 🔗</a>', rate
        return text, rate

    header_cells = "".join(f'<th style="text-align:left;padding:6px 12px;">{s}</th>' for s in present_sources)
    html_rows = []
    for hotel in all_hotels:
        hotel_df = df[df["Hotel"] == hotel.name]
        for check_in in sorted(hotel_df["Date"].unique()):
            day_df = hotel_df[hotel_df["Date"] == check_in]
            cells, rates = [], []
            for source in present_sources:
                html, rate = _rate_cell(day_df[day_df["source"] == source])
                cells.append(html)
                rates.append(rate)
            cheapest = min([r for r in rates if r is not None], default=None)
            cell_html = "".join(
                f'<td style="padding:6px 12px;{"background:#d4edda;font-weight:bold;" if r == cheapest and r is not None else ""}">{h}</td>'
                for h, r in zip(cells, rates)
            )
            role = "Primary" if hotel.is_primary else "Competitor"
            html_rows.append(
                f'<tr><td style="padding:6px 12px;white-space:nowrap;"><b>{hotel.name}</b><br>'
                f'<span style="color:#888;font-size:0.85em;">{role}</span></td>'
                f'<td style="padding:6px 12px;white-space:nowrap;">{check_in}</td>{cell_html}</tr>'
            )

    table_html = (
        '<table style="border-collapse:collapse;width:100%;">'
        f'<thead><tr><th style="text-align:left;padding:6px 12px;">Hotel</th>'
        f'<th style="text-align:left;padding:6px 12px;">Date</th>{header_cells}</tr></thead>'
        f"<tbody>{''.join(html_rows)}</tbody></table>"
    )
    st.markdown(table_html, unsafe_allow_html=True)

    if "Brand.com" in df["source"].unique():
        st.subheader("Rate parity (Brand.com vs. cheapest third-party site)")
        st.caption(
            "Mirrors the Alert Catalog's Parity violation rule: a third-party site undercutting the brand-direct rate. "
            "Brand-direct reads are best-effort (see brand_scraper.py) — treat gaps as directional, and "
            "check the source URLs before acting on one."
        )
        available = df[df["available"] == True]  # noqa: E712 - pandas bool comparison, not identity
        brand_rows = available[available["source"] == "Brand.com"][["Hotel", "Date", "lowest_rate", "url"]]
        brand_rows = brand_rows.rename(columns={"lowest_rate": "Brand rate", "url": "Brand URL"})
        ota_rows = available[available["source"] != "Brand.com"]
        cheapest_ota = (
            ota_rows.sort_values("lowest_rate").groupby(["Hotel", "Date"], as_index=False).first()
            [["Hotel", "Date", "source", "lowest_rate", "url"]]
            .rename(columns={"source": "Cheapest third-party", "lowest_rate": "Third-party rate", "url": "Third-party URL"})
        )
        parity = brand_rows.merge(cheapest_ota, on=["Hotel", "Date"], how="inner")
        if parity.empty:
            st.caption("No overlapping rows yet — fetch at least one third-party source alongside Brand.com.")
        else:
            parity["Gap ($)"] = parity["Third-party rate"] - parity["Brand rate"]
            parity["Status"] = parity["Gap ($)"].apply(
                lambda g: "🔴 Parity violation" if g < -0.5 else ("🟢 Brand wins" if g > 0.5 else "⚪ In parity")
            )
            st.dataframe(
                parity[["Hotel", "Date", "Brand rate", "Cheapest third-party", "Third-party rate", "Gap ($)", "Status", "Brand URL", "Third-party URL"]],
                width="stretch",
                column_config={
                    "Brand URL": st.column_config.LinkColumn("Brand URL"),
                    "Third-party URL": st.column_config.LinkColumn("Third-party URL"),
                    "Brand rate": st.column_config.NumberColumn(format="$%.0f"),
                    "Third-party rate": st.column_config.NumberColumn(format="$%.0f"),
                },
            )
else:
    st.info("Set your dates and click **Fetch Rates** in the sidebar to pull live rates.")
