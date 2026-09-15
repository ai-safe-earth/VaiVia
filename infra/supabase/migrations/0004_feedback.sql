-- Message feedback: a thumbs vote (and optional comment) on an assistant
-- answer, keyed to the message so a downvote joins straight back to
-- messages.content / intent / result_refs for eval review.
--
-- Unlike route_favorites, message_id and conversation_id ARE real foreign
-- keys: chat rows are never wiped and recreated, so the reference holds.
-- One row per (user, message); a re-vote is an upsert done by the backend.
--
-- Idempotent, like every migration here: re-running is the normal case.

create table if not exists message_feedback (
    user_id         uuid not null references auth.users (id) on delete cascade,
    message_id      uuid not null references public.messages (id) on delete cascade,
    conversation_id uuid not null references public.conversations (id) on delete cascade,
    vote            smallint not null check (vote in (-1, 1)),
    comment         text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    primary key (user_id, message_id)
);

-- The one read pattern: this user's feedback, newest first.
create index if not exists idx_message_feedback_user
    on message_feedback (user_id, created_at desc);

alter table message_feedback enable row level security;

-- create policy has no IF NOT EXISTS, so drop first to stay re-runnable.
drop policy if exists "Users read own feedback" on message_feedback;
create policy "Users read own feedback" on message_feedback
    for select using (auth.uid() = user_id);

-- BOTH the policy and the grant are required (see 0002): a policy can only
-- narrow a privilege that exists, and new Supabase projects revoke by
-- default. anon is deliberate — the policy hands it zero rows.
grant select on table message_feedback to anon, authenticated;

-- Writes go through the backend as the owner (no insert/update policies),
-- the same split as conversations and messages.
