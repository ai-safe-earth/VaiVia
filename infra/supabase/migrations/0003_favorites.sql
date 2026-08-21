-- Route favorites: which catalogue routes a user has saved.
--
-- Supabase, not the social layer's MongoDB (docs/social-layer.md defers likes
-- until that feature is specified; a personal favorite is account data, and
-- Postgres puts the ownership check in the database where Mongo would put it
-- in application code). route_id is TEXT, not a foreign key: the catalogue's
-- :Route nodes are wiped and recreated per export, and the id is
-- geometry-derived precisely so rows like these survive a rebuild
-- (docs/route-document.md). A favorite whose route left the catalogue is
-- reported by the API as missing, never silently dropped.
--
-- Idempotent, like every migration here: re-running is the normal case.

create table if not exists route_favorites (
    user_id    uuid not null references auth.users (id) on delete cascade,
    route_id   text not null,
    created_at timestamptz not null default now(),
    primary key (user_id, route_id)
);

-- The one read pattern: this user's favorites, newest first.
create index if not exists idx_route_favorites_user
    on route_favorites (user_id, created_at desc);

alter table route_favorites enable row level security;

-- create policy has no IF NOT EXISTS, so drop first to stay re-runnable.
drop policy if exists "Users read own favorites" on route_favorites;
create policy "Users read own favorites" on route_favorites
    for select using (auth.uid() = user_id);

-- BOTH the policy and the grant are required (see 0002): a policy can only
-- narrow a privilege that exists, and new Supabase projects revoke by
-- default. anon is deliberate — the policy hands it zero rows.
grant select on table route_favorites to anon, authenticated;

-- Writes go through the backend as the owner (no insert/delete policies),
-- the same split as conversations and messages.
