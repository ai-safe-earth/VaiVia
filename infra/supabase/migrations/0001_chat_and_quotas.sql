-- Chat history, usage ledger, and daily quotas.
-- Apply with the Supabase CLI (supabase db push) once the project exists.
-- auth.users is managed by Supabase Auth.

create table if not exists conversations (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    title       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create table if not exists messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations (id) on delete cascade,
    role            text not null check (role in ('user', 'assistant', 'system')),
    content         text not null,
    intent          jsonb,          -- validated intent JSON (assistant turns)
    result_refs     jsonb,          -- trail/route ids grounding the answer
    created_at      timestamptz not null default now()
);

create table if not exists usage_ledger (
    id             bigint generated always as identity primary key,
    user_id        uuid not null references auth.users (id) on delete cascade,
    message_id     uuid references messages (id) on delete set null,
    model          text not null,
    input_tokens   integer not null default 0,
    output_tokens  integer not null default 0,
    cost_usd       numeric(10, 6) not null default 0,
    created_at     timestamptz not null default now()
);

create table if not exists daily_quotas (
    user_id      uuid not null references auth.users (id) on delete cascade,
    day          date not null,
    tokens_used  bigint not null default 0,
    requests     integer not null default 0,
    primary key (user_id, day)
);

create index if not exists idx_conversations_user on conversations (user_id, updated_at desc);
create index if not exists idx_messages_conversation on messages (conversation_id, created_at);
create index if not exists idx_usage_user_day on usage_ledger (user_id, created_at);

-- Row-level security: users see only their own rows; the backend uses the
-- service-role key and bypasses RLS.
alter table conversations enable row level security;
alter table messages enable row level security;
alter table usage_ledger enable row level security;
alter table daily_quotas enable row level security;

create policy "own conversations" on conversations
    for select using (auth.uid() = user_id);
create policy "own messages" on messages
    for select using (
        exists (
            select 1 from conversations c
            where c.id = conversation_id and c.user_id = auth.uid()
        )
    );
create policy "own usage" on usage_ledger
    for select using (auth.uid() = user_id);
create policy "own quotas" on daily_quotas
    for select using (auth.uid() = user_id);
