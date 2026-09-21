# Adding a test hotel

Two ways to add a hotel to the POC — pick based on whether it should be permanent or just for one test.

## Option A — through the app (no code, not permanent unless Supabase is connected)

In the sidebar, under **"Add a hotel"**, fill in the form and click **Add to comparison**. If Supabase is
configured (see [DEPLOY.md](DEPLOY.md)), it's saved and shows up for everyone who opens the link from then on.
If not, it only lasts for your current browser session.

## Option B — editing hotels.py (permanent, part of the baked-in set)

Add a new `Hotel(...)` entry to the `HOTELS` list in [hotels.py](hotels.py), commit, and push. This is how the
two built-in hotels (Fairfield Inn & Suites, Crowne Plaza) got there.

---

## What each field means

| Field | Required? | What it's for |
|---|---|---|
| **Hotel name** | Yes | Shown everywhere in the app. Used to search Booking.com if no direct URL is given. |
| **City, State** | Only if no address given | Combined with the name for a Booking.com search, e.g. "Hampton Inn, Burlingame CA". |
| **Booking.com URL** | No, but recommended | The hotel's own Booking.com page. If given, skips the search step entirely — faster and more reliable than searching by name. |
| **Street address** | No, but recommended | Used for Expedia, which is searched by address + `sort=DISTANCE` rather than by name (Expedia's name search is unreliable). |
| **Brand** | No | Just a label shown in the UI (e.g. "Hilton", "Marriott"). Doesn't affect scraping. |
| **Brand-direct booking page URL** | No | The hotel's official brand-site page (marriott.com, hilton.com, etc.). Enables the "Brand.com" rate-parity check. Without it, that hotel just won't have a Brand.com column value. |

Nothing here is strictly required except the hotel name — but a fetch with only a name will be slower (has to
search Booking.com) and less accurate on Expedia (no address to match against). Filling in the URL + address
fields is worth the extra minute.

---

## Real example 1 — a hotel already in the app (Crowne Plaza)

This is the actual competitor built into `hotels.py`, so you can see exactly what real, working values look like:

| Field | Value |
|---|---|
| Hotel name | `Crowne Plaza San Francisco International Airport` |
| Street address | `1177 Airport Blvd, Burlingame, CA 94010` |
| Booking.com URL | `https://www.booking.com/hotel/us/san-fransisco-airport-burlingame.html` |
| Brand | `IHG` |
| Brand-direct booking page URL | `https://www.ihg.com/crowneplaza/hotels/us/en/burlingame/urlca/hoteldetail` |

## Real example 2 — adding one that isn't in the app yet (Hampton Inn San Francisco Airport)

Walking through how you'd actually fill in the sidebar form for a new hotel, using real values found by a quick
web search (this is genuinely how the built-in hotels were researched too):

1. **Hotel name**: `Hampton Inn San Francisco Airport`
2. **Street address**: search `"Hampton Inn San Francisco Airport" address` → `300 Gateway Blvd, South San Francisco, CA 94080`
3. **Booking.com URL**: search `"Hampton Inn San Francisco Airport" booking.com` → `https://www.booking.com/hotel/us/hampton-inn-san-francisco-airport.html`
4. **Brand**: it's a Hilton property → `Hilton`
5. **Brand-direct booking page URL**: search `"Hampton Inn San Francisco Airport" hilton.com` → `https://www.hilton.com/en/hotels/sfohhhx-hampton-san-francisco-airport/`

Paste those five values into the form exactly as above and click **Add to comparison** — it'll show up in the
"POC hotel set" table immediately and get included in the next **Fetch Rates** click.

### Tips for finding these values yourself

- **Address**: search `<hotel name> address`. First result (usually the hotel's own site, Google, or Yelp) has it.
- **Booking.com URL**: search `<hotel name> booking.com`, or go to booking.com and search the hotel name directly — copy the URL of its detail page (strip off anything after `.html`, no query params needed).
- **Brand-direct URL**: figure out the brand chain first (Marriott, Hilton, IHG, Wyndham, Best Western, etc. — usually obvious from the name, e.g. "Hampton Inn" = Hilton, "Holiday Inn Express" = IHG), then search `<hotel name> <brand>.com`.

### A caveat on brand-direct rates

Even with a good URL, the **Brand.com** column is best-effort — see `brand_scraper.py`'s docstring. It reads
whatever the brand's booking widget shows via an LLM (no site-specific parser like Booking.com/Expedia have), so
it can occasionally miss or show a rate for the wrong dates. Booking.com and Expedia are the reliable columns;
Brand.com is the "nice when it works" one.
