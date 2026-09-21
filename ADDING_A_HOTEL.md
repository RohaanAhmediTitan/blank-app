# Adding a test hotel

## Real example 1 — a hotel already in the app (Crowne Plaza)

This is the actual competitor built into `hotels.py`, so you can see exactly what real, working values look like:

| Field | Value |
|---|---|
| Hotel name | `Crowne Plaza San Francisco International Airport` |
| Street address | `1177 Airport Blvd, Burlingame, CA 94010` |
| Booking.com URL | `https://www.booking.com/hotel/us/san-fransisco-airport-burlingame.html` |
| Expedia URL | `https://www.expedia.com/San-Francisco-Hotels-Crowne-Plaza-San-Francisco-Airport.h28502.Hotel-Information` |
| Brand | `IHG` |
| Brand-direct booking page URL | `https://www.ihg.com/crowneplaza/hotels/us/en/burlingame/urlca/hoteldetail` |

## Real example 2 — adding one that isn't in the app yet (Hampton Inn San Francisco Airport)

Walking through how you'd actually fill in the sidebar form for a new hotel, using real values found by a quick
web search (this is genuinely how the built-in hotels were researched too):

1. **Hotel name**: `Hampton Inn San Francisco Airport`
2. **Street address**: search `"Hampton Inn San Francisco Airport" address` → `300 Gateway Blvd, South San Francisco, CA 94080`
3. **Booking.com URL**: search `"Hampton Inn San Francisco Airport" booking.com` → `https://www.booking.com/hotel/us/hampton-inn-san-francisco-airport.html`
4. **Expedia URL**: search `"Hampton Inn San Francisco Airport" expedia.com` → `https://www.expedia.com/San-Francisco-Hotels-Hampton-Inn-San-Francisco-Airport.h437098.Hotel-Information`
5. **Brand**: it's a Hilton property → `Hilton`
6. **Brand-direct booking page URL**: search `"Hampton Inn San Francisco Airport" hilton.com` → `https://www.hilton.com/en/hotels/sfohhhx-hampton-san-francisco-airport/`

Paste those six values into the form exactly as above and click **Add to comparison** — it'll show up in the
"POC hotel set" table immediately and get included in the next **Fetch Rates** click.

Giving both the Booking.com and Expedia URLs directly (rather than leaving them blank) matters more than it
might look — Expedia's address-based search sorts by distance and can confidently return the *wrong* nearby
hotel instead of this one, with no error to flag it. A direct URL skips that risk entirely.
