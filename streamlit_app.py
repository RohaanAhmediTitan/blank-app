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

import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from hotels import HOTELS, Hotel
from serpapi_client import ALL_SOURCE_NAMES, get_all_source_rates
import supabase_client

# All three sources now come from one SerpApi call per hotel/date (see
# serpapi_client.get_all_source_rates) instead of three separate
# Firecrawl-backed scrapes, so there's no longer a shared per-account
# rate limit to protect against with a low worker count — this just bounds
# how many concurrent requests hit SerpApi at once.
MAX_WORKERS = 6

# Plain REST calls via SerpApi, not a page render + LLM extraction or
# hand-parsed HTML — measured against the live sites, one call per
# hotel/date returns all three sources at once.
SECONDS_PER_HOTEL_DATE_CALL = 4

st.set_page_config(page_title="RevRadar Rate Shop POC", layout="wide")
st.title("RevRadar — Live Rate Comparison POC")
st.caption(
    "Primary: **Fairfield Inn & Suites San Francisco Airport/Millbrae** vs. its real SFO-airport compset. "
    "Rates are fetched live from Booking.com, Expedia, and each hotel's own brand-direct site."
)
if not supabase_client.is_enabled():
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
            expedia_url=h.get("expedia_url") or "",
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
        ALL_SOURCE_NAMES,
        default=ALL_SOURCE_NAMES,
        help="All sources come from the same Google Hotels lookup per hotel/date — expect gaps whenever "
        "a source doesn't have a listing for that property/date in Google's feed. Rates shown are the "
        "public/Best Available Rate, not a loyalty-member discount — the same convention industry "
        "rate-shopping tools use, since parity contracts are scoped to the public rate.",
    )

    st.divider()
    st.subheader("Add a hotel (e.g. one the client names live)")
    with st.expander("📖 How to add a test hotel (guide, with real examples)"):
        guide_path = Path(__file__).parent / "ADDING_A_HOTEL.md"
        st.markdown(guide_path.read_text(encoding="utf-8"))
    with st.form("add_hotel_form", clear_on_submit=True):
        new_name = st.text_input("Hotel name")
        new_city = st.text_input("City, State (used to build the address below if left blank)")
        new_booking_url = st.text_input(
            "Booking.com URL (optional — used as a display link only if Google Hotels' own listing has none)"
        )
        new_address = st.text_input("Street address (recommended — this is what's used to look up the hotel)")
        new_expedia_url = st.text_input(
            "Expedia URL (optional — used as a display link only if Google Hotels' own listing has none)",
            help="All three rates are looked up together by hotel name + address via Google Hotels, so a "
            "precise address is what actually determines a correct match, not these URLs.",
        )
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
                    expedia_url=new_expedia_url.strip(),
                    brand_name=new_brand_name.strip(),
                    brand_url=new_brand_url.strip(),
                    brand_domain=brand_domain,
                )
            )
            supabase_client.save_client_hotel(
                name=new_name,
                address=resolved_address,
                booking_url=new_booking_url.strip(),
                expedia_url=new_expedia_url.strip(),
                brand_name=new_brand_name.strip(),
                brand_url=new_brand_url.strip(),
                brand_domain=brand_domain,
            )
            st.success(f"Added {new_name} — will be included in the next fetch.")

    if st.session_state["custom_hotels"]:
        st.caption("Ad-hoc hotels added (persisted if Supabase is connected):")
        for i, h in enumerate(list(st.session_state["custom_hotels"])):
            hcol1, hcol2 = st.columns([5, 1])
            with hcol1:
                st.caption(f"• {h.name}")
            with hcol2:
                # Index in the key, not just the name — two hotels can share a
                # name (e.g. added twice by mistake), and Streamlit widget
                # keys must be unique or the whole app crashes.
                if st.button("🗑️", key=f"remove_hotel_{i}_{h.name}", help=f"Remove {h.name}"):
                    # Removes every hotel with this name, including
                    # duplicates — intentional, since the point is "get rid
                    # of this one," not "get rid of exactly one copy of it."
                    st.session_state["custom_hotels"] = [
                        x for x in st.session_state["custom_hotels"] if x.name != h.name
                    ]
                    supabase_client.delete_client_hotel(h.name)
                    st.rerun()

    all_hotels_preview: list[Hotel] = HOTELS + st.session_state["custom_hotels"]
    n_hotels = len(all_hotels_preview)
    # One SerpApi call per hotel/date returns all three sources at once, so
    # the estimate no longer varies by which sources are selected — only
    # whether at least one is.
    est_seconds = (n_hotels * num_nights * SECONDS_PER_HOTEL_DATE_CALL) if sources else 0.0
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


def _render_results(rows: list[dict], all_hotels: list[Hotel], completed_at: str | None = None, live: bool = False) -> None:
    """Renders the rates grid + parity section for whatever rows are available
    so far. Called both mid-fetch (live=True, partial rows — one more date's
    worth lands each time) and once the fetch is done (live=False, full rows),
    so the client sees results appear night by night instead of staring at a
    bare progress bar until everything finishes."""
    if not rows:
        return
    df = pd.DataFrame(rows)

    if live:
        st.caption(f"⏳ Fetching — filling in below as each night completes ({len(df)} request(s) so far).")
    else:
        st.success(
            f"Last full fetch completed at **{completed_at}** — "
            f"{len(df)} live requests made just now. Re-click **Fetch Rates** any time to prove it isn't cached."
        )

    st.subheader("Rates by hotel and night")
    st.caption(
        "One row per hotel/night. Each rate is a clickable link straight to the exact page our scraper "
        "read it from — click 🔗 to verify against the live site yourself. Cheapest rate in each row is highlighted."
    )

    present_sources = [s for s in ALL_SOURCE_NAMES if s in df["source"].unique()]

    def _safe_href(url) -> str | None:
        # Anyone can add a hotel via the open add-hotel form (client_hotels'
        # RLS allows any insert), and its URL fields flow straight into this
        # raw HTML table's href attributes below — so only allow http(s)
        # links and escape quotes, rather than trust the string as-is.
        if not isinstance(url, str) or not url:
            return None
        if urlparse(url).scheme not in ("http", "https"):
            return None
        return html.escape(url, quote=True)

    def _rate_cell(sub: pd.DataFrame) -> tuple[str, float | None]:
        """Returns (html for this cell, numeric rate for cheapest-highlighting)."""
        if sub.empty:
            return "—", None
        row = sub.iloc[0]
        if not row.get("available") or pd.isna(row.get("lowest_rate")):
            error = row.get("error")
            has_error = pd.notna(error) and error  # NaN is truthy in Python — pandas pads
            # missing "error" keys with NaN when other rows in the batch do have one, so a
            # plain `if error` here misread every genuine sold-out row as a fetch failure.
            label = "Couldn't fetch ⚠️" if has_error else "Sold out"
            title = f' title="{html.escape(str(error), quote=True)}"' if has_error else ""
            safe_url = _safe_href(row.get("url"))
            # Link even when there's no rate, so "Sold out" is checkable against
            # the live page instead of just having to be taken on faith.
            if safe_url:
                inner = f'<a href="{safe_url}" target="_blank" rel="noopener" style="color:#999;text-decoration:none;">{label} 🔗</a>'
            else:
                inner = label
            return f'<span{title}>{inner}</span>', None
        rate = row["lowest_rate"]
        safe_url = _safe_href(row.get("url"))
        text = f"${rate:,.0f}"
        fallback_from = row.get("fallback_from")
        # NaN-safe for the same reason as `error` above — pandas pads this
        # column with NaN on every row that didn't need a fallback.
        if pd.notna(fallback_from) and fallback_from:
            text += f' <span style="color:#999;font-size:0.8em;">(via {html.escape(str(fallback_from))})</span>'
        if safe_url:
            return f'<a href="{safe_url}" target="_blank" rel="noopener" style="text-decoration:none;">{text} 🔗</a>', rate
        return text, rate

    header_cells = "".join(f'<th style="text-align:left;padding:6px 12px;">{s}</th>' for s in present_sources)
    html_rows = []
    for hotel in all_hotels:
        hotel_df = df[df["Hotel"] == hotel.name]
        for check_in in sorted(hotel_df["Date"].unique()):
            day_df = hotel_df[hotel_df["Date"] == check_in]
            cells, rates = [], []
            for source in present_sources:
                # Named cell_html, not html — a loop variable named `html`
                # here would shadow the `html` module imported above for the
                # rest of the script (confirmed live: the first cell renders
                # fine, then every call after it hits AttributeError since
                # `html.escape` becomes a string, not the module).
                cell_html, rate = _rate_cell(day_df[day_df["source"] == source])
                cells.append(cell_html)
                rates.append(rate)
            cheapest = min([r for r in rates if r is not None], default=None)
            cell_html = "".join(
                f'<td style="padding:6px 12px;{"background:#d4edda;font-weight:bold;" if r == cheapest and r is not None else ""}">{h}</td>'
                for h, r in zip(cells, rates)
            )
            role = "Primary" if hotel.is_primary else "Competitor"
            html_rows.append(
                f'<tr><td style="padding:6px 12px;white-space:nowrap;"><b>{html.escape(hotel.name)}</b><br>'
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
            "Brand-direct reads depend on Google Hotels having that brand's price for this property/date "
            "(see serpapi_client.py) — treat gaps as directional, and check the source URLs before acting on one. "
            "Brand.com shown here is the public/Best Available Rate, matching the convention rate-shopping tools "
            "use for parity checks — it will run lower than a brand site's own headline price if that site "
            "defaults to a loyalty-member discount (e.g. Marriott's free-enrollment rate)."
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


if fetch_clicked:
    dates = [start_date + timedelta(days=i) for i in range(num_nights)]

    # One SerpApi call per hotel/date returns every source in
    # ALL_SOURCE_NAMES together (see serpapi_client.get_all_source_rates) —
    # no separate resolve phase needed (that was only ever for finding each
    # hotel's Booking.com/Expedia URL before scraping it directly), and no
    # per-source job type either, since every source comes from one lookup.
    def _fetch_hotel_date(hotel: Hotel, check_in: date) -> list[dict]:
        check_out = check_in + timedelta(days=1)
        try:
            by_source = get_all_source_rates(
                hotel.name,
                hotel.address,
                check_in,
                check_out,
                guests,
                brand_name=hotel.brand_name,
                booking_url=hotel.booking_url,
                expedia_url=hotel.expedia_url,
                brand_url=hotel.brand_url,
            )
        except Exception as exc:  # noqa: BLE001 - surface any lookup failure in the grid, demo keeps going
            error = str(exc)
            by_source = {
                key: {"source": key, "url": None, "available": False, "lowest_rate": None, "error": error}
                for key in ALL_SOURCE_NAMES
            }
        fetched_at = datetime.now().strftime("%H:%M:%S")
        return [
            {"Hotel": hotel.name, "Date": check_in, "Fetched at": fetched_at, **by_source[source]}
            for source in sources
        ]

    # Grouped by date (not one flat list) so each date's jobs can be waited
    # on and rendered as its own batch below — the client sees nights land
    # one at a time instead of a blank grid until every date is done.
    jobs_by_date: dict[date, list[Hotel]] = {check_in: list(all_hotels) for check_in in dates}

    total_calls = sum(len(v) for v in jobs_by_date.values())
    rows: list[dict] = []
    progress = st.progress(0.0, text="Starting fetch...")
    done = 0
    results_area = st.empty()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for check_in in dates:
            date_hotels = jobs_by_date[check_in]
            if not date_hotels:
                continue
            futures = {executor.submit(_fetch_hotel_date, hotel, check_in): hotel for hotel in date_hotels}
            for future in as_completed(futures):
                hotel = futures[future]
                rows.extend(future.result())
                done += 1
                progress.progress(done / max(total_calls, 1), text=f"Fetched {done}/{total_calls} — last: {hotel.name} — {check_in}")
            # This date is fully in (every hotel for it) — show it now rather
            # than waiting for every remaining date too.
            with results_area.container():
                _render_results(rows, all_hotels, live=True)

    progress.empty()
    st.session_state["rate_rows"] = rows
    st.session_state["last_fetch_completed_at"] = datetime.now().strftime("%H:%M:%S")
    supabase_client.save_rate_shop_rows(rows)
    with results_area.container():
        _render_results(rows, all_hotels, completed_at=st.session_state["last_fetch_completed_at"], live=False)

elif "rate_rows" in st.session_state:
    _render_results(
        st.session_state["rate_rows"],
        all_hotels,
        completed_at=st.session_state.get("last_fetch_completed_at"),
        live=False,
    )
else:
    st.info("Set your dates and click **Fetch Rates** in the sidebar to pull live rates.")
