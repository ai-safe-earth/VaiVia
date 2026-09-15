-- Per-route feedback (owner decision 2026-09-08): a thumbs on ONE route card
-- as well as on the answer as a whole. The route id localises the complaint —
-- WHICH card did not belong, not merely that the turn was wrong — which is
-- what a retrieval expectation is written from. The pin stays positive and
-- stays hand-written (Phase 8 E2); this table only says where to look.
--
-- '' is the answer-level vote 0004 already stored, so existing rows keep their
-- meaning and the key simply widens.
--
-- No foreign key on route_id, deliberately, exactly as route_favorites:
-- catalogue ids are content-derived and rebuilt, and a vote about a route that
-- has left the catalogue is still worth reading.
--
-- Idempotent, like every migration here: re-running is the normal case.

alter table message_feedback add column if not exists route_id text not null default '';

-- ADD PRIMARY KEY has no IF NOT EXISTS, so the widening is guarded on the key
-- it would produce rather than attempted blind.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.message_feedback'::regclass
          and contype = 'p'
          and array_length(conkey, 1) = 3
    ) then
        alter table message_feedback drop constraint if exists message_feedback_pkey;
        alter table message_feedback add primary key (user_id, message_id, route_id);
    end if;
end $$;
