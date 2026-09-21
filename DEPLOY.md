# Deploying the scraping POC — free hosting

**Scope note (2026-09-21):** the original [PLAN.md](../docs/poc-scraping-demo/PLAN.md) explicitly scoped this POC as
screen-share-only, no persistence. That's since changed — the client wants a link they can open themselves to add
test hotels and see results persist. This doc covers what changed and how to actually put it online for free.

## What changed from the original POC

- **Persistence**: added Supabase (`supabase_client.py`, schema in [sql/schema.sql](sql/schema.sql)). Hotels added
  through the app and every fetch's results are now saved, not just held in Streamlit's in-memory session state.
- **Brand-direct rate ("rate parity")**: added `brand_scraper.py`, using Firecrawl's `/v2/extract` (LLM-based
  reading of the rendered page) since there's no single HTML structure to parse across Marriott/Hilton/IHG/
  Wyndham/Best Western. It's best-effort — see the caveats in that file's docstring and in the app's Rate Parity
  section.
- **No access gate.** The client asked to skip this for now — the deployed link is open to anyone who has it.
  Worth revisiting before wide distribution: every page load with "Fetch Rates" clicked costs real Firecrawl
  credits (see PLAN.md's original ToS/cost caveats, still true).

## Step 1 — Supabase (free tier, for persistence)

1. Sign up at [supabase.com](https://supabase.com) and create a new project (free tier: 500MB DB, paused after a
   week of inactivity but wakes up on the next request — fine for a demo).
2. In the dashboard: **SQL Editor → New query**, paste the contents of [sql/schema.sql](sql/schema.sql), **Run**.
   This creates `client_hotels` and `rate_shops` with RLS policies open enough for the app's anon key to read/write.
3. **Project Settings → API**: copy the **Project URL** and the **anon public** key (not the `service_role` key —
   that one bypasses RLS and should never ship in a deployed app's secrets).
4. Put those in `.env` locally as `SUPABASE_URL` / `SUPABASE_KEY` (see `.env.example`) to test before deploying.

If you skip this step entirely, the app still runs — `supabase_client.py` no-ops when the env vars are unset, and
you'll see the "session-only" warning banner in the app instead of "connected to Supabase."

## Step 2 — Streamlit Community Cloud (free hosting)

This is the free option that fits best here: it's built for exactly this (a Streamlit app on a public URL), needs
no server management, deploys straight from a GitHub repo, and has a built-in secrets manager so
`FIRECRAWL_API_KEY`/`SUPABASE_URL`/`SUPABASE_KEY` never touch the repo itself.

1. Push this project to a GitHub repo (the `scraping-poc/` folder needs to be reachable — either its own repo, or
   this repo with `scraping-poc/streamlit_app.py` as the entry point).
2. Go to [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub → **New app**.
3. Pick the repo/branch, set **Main file path** to `scraping-poc/streamlit_app.py`.
4. Before or after first deploy, open **App settings → Secrets** and paste:
   ```toml
   FIRECRAWL_API_KEY = "fc-..."
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "eyJ..."
   ```
   Streamlit injects these as environment variables at runtime — `firecrawl_client.py` and `supabase_client.py`
   both read them via `os.environ`, same as `.env` locally, so no code changes are needed between local and
   deployed.
5. Deploy. You get a URL like `https://<something>.streamlit.app` to hand to the client.

**Free-tier limits worth knowing:** Community Cloud apps sleep after ~ a few days of no traffic (an idle visitor
just sees a "waking up" screen for a few seconds — no data loss, nothing to restart manually) and public apps run
on shared compute, which is fine for this POC's traffic (one client, occasional live fetches) but not for
production scale.

### Alternative free options, if Streamlit Cloud doesn't fit

- **Render (free web service tier)**: works for a Streamlit app packaged as a standard Python web service, but the
  free tier spins down after 15 min idle and takes ~30–60s to cold-start on the next visit — more noticeable than
  Streamlit Cloud's sleep behavior for a live demo.
- **Fly.io free allowance**: more setup (Dockerfile, `fly.toml`), better if you outgrow Streamlit Cloud's shared
  compute, overkill for this POC.

Streamlit Community Cloud is the right default here — least setup, purpose-built for this exact app shape.

## Step 3 — sanity check before sending the link

- Open the deployed URL yourself, click **Fetch Rates** with a small date range, confirm rows come back and the
  "📌 Persistence: connected to Supabase" banner shows (not the session-only warning).
- Refresh the page — the demo hotel set plus any hotel you added should still be there (proves persistence works,
  not just that the fetch worked).
- In the Supabase dashboard, **Table Editor → rate_shops**, confirm rows landed there too.
