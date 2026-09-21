-- RevRadar scraping POC — Supabase schema
-- Run in the Supabase dashboard: Project -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run any time (e.g. after pulling a schema update) — every statement
-- is idempotent.

create table if not exists client_hotels (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  address text,
  booking_url text,
  expedia_url text,
  brand_name text,
  brand_url text,
  brand_domain text,
  created_at timestamptz not null default now()
);

-- Safe to re-run: adds expedia_url if this table already existed without it
-- (added 2026-09-21 — Expedia's address-based search can match the wrong
-- nearby hotel, so a direct URL is now offered same as booking_url).
alter table client_hotels add column if not exists expedia_url text;

create table if not exists rate_shops (
  id bigint generated always as identity primary key,
  hotel_name text not null,
  source text not null,               -- 'Booking.com' | 'Expedia' | 'Brand direct'
  check_in date not null,
  fetched_at timestamptz not null default now(),
  available boolean not null default false,
  lowest_rate numeric,
  currency text,
  room_type text,
  url text,
  error text
);

create index if not exists rate_shops_hotel_date_idx on rate_shops (hotel_name, check_in);

-- Row Level Security: the app connects with Supabase's public "anon" key
-- (safe to ship in a deployed app's secrets — it has no power beyond what
-- these policies grant), so access is opened up only for these two demo
-- tables. Nothing else exists in a fresh Supabase project. Tighten this
-- (e.g. require auth, restrict inserts) before this ever holds real,
-- non-demo client data.
alter table client_hotels enable row level security;
alter table rate_shops enable row level security;

-- drop-then-create makes this safe to re-run (Postgres has no
-- CREATE POLICY IF NOT EXISTS)
drop policy if exists "anon read client_hotels" on client_hotels;
drop policy if exists "anon insert client_hotels" on client_hotels;
drop policy if exists "anon read rate_shops" on rate_shops;
drop policy if exists "anon insert rate_shops" on rate_shops;

create policy "anon read client_hotels" on client_hotels for select using (true);
create policy "anon insert client_hotels" on client_hotels for insert with check (true);
create policy "anon read rate_shops" on rate_shops for select using (true);
create policy "anon insert rate_shops" on rate_shops for insert with check (true);
