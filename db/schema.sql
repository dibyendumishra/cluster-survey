-- Run this once in Supabase → SQL Editor

-- Clusters table: one row per polygon your algorithm outputs
create table if not exists clusters (
  id            text primary key,          -- e.g. "CLU-001"
  name          text,
  block         text,
  district      text,
  state         text,
  households    integer,
  priority_score numeric(4,3),             -- 0.000 to 1.000
  area_ha       numeric(8,2),
  geometry      jsonb not null,            -- GeoJSON geometry object
  status        text default 'pending'     -- pending | confirmed | flagged
    check (status in ('pending','confirmed','flagged')),
  flagged_reason text,
  algorithm     text default 'unknown',     -- dbscan | hdbscan | sam | existing
  batch_id      text,                      -- lets you upload multiple runs
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

-- Responses table: audit trail of every surveyor action
create table if not exists responses (
  id            bigserial primary key,
  cluster_id    text references clusters(id),
  action        text not null              -- confirmed | flagged
    check (action in ('confirmed','flagged')),
  reason        text,                      -- filled when action = flagged
  surveyor_id   text,                      -- simple name/ID string for now
  responded_at  timestamptz default now()
);

-- Auto-update updated_at on clusters
create or replace function update_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger clusters_updated_at
  before update on clusters
  for each row execute procedure update_updated_at();

-- Allow public read on clusters (surveyors don't log in)
-- Allow public insert on responses
alter table clusters  enable row level security;
alter table responses enable row level security;

create policy "public read clusters"
  on clusters for select using (true);

create policy "public update cluster status"
  on clusters for update using (true);

create policy "public insert responses"
  on responses for insert with check (true);

create policy "public read responses"
  on responses for select using (true);
