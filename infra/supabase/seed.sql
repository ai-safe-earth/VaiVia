-- Local development seed. Applied by the Supabase CLI on `supabase start` and
-- `supabase db reset` (see [db.seed] in config.toml). It is NEVER applied to a
-- hosted project: scripts/apply_migrations.py globs migrations/*.sql only, and
-- this file is not one of them.
--
-- It creates the account the app and the Playwright suite sign in as, so a
-- clone plus `supabase start` is a working login rather than a manual step in
-- a README that goes stale.
--
--     dev@vaivia.local / vaivia-local-dev
--
-- Writing those here is deliberate, and it is not the mistake that the exposed
-- production credentials were. This stack binds to loopback, holds nothing but
-- fixture data, and is recreated by `supabase db reset`; a shared, documented
-- local account is what makes the setup reproducible. It follows that this
-- password must never be used for anything that is not this stack.

-- The four token columns are set to '' rather than left NULL. They are
-- nullable in the schema, but GoTrue scans them into plain Go strings, so a
-- NULL fails the scan and every sign-in comes back as
-- "Database error querying schema" -- an error that points at the schema when
-- the schema is fine and the row is not.
insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    confirmation_token, recovery_token, email_change, email_change_token_new,
    created_at, updated_at
)
values (
    '00000000-0000-0000-0000-000000000000',
    '00000000-0000-4000-8000-000000000001',
    'authenticated',
    'authenticated',
    'dev@vaivia.local',
    -- bcrypt, generated here rather than pasted, so no hash of a real password
    -- can be copied into this file by accident.
    crypt('vaivia-local-dev', gen_salt('bf')),
    now(),
    '{"provider": "email", "providers": ["email"]}'::jsonb,
    '{"email_verified": true}'::jsonb,
    '', '', '', '',
    now(),
    now()
)
on conflict (id) do nothing;

-- GoTrue will not sign in a user with no matching identity row, even when the
-- password verifies: the email provider is resolved through auth.identities.
insert into auth.identities (
    provider_id, user_id, identity_data, provider, last_sign_in_at,
    created_at, updated_at
)
values (
    '00000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-000000000001',
    jsonb_build_object(
        'sub', '00000000-0000-4000-8000-000000000001',
        'email', 'dev@vaivia.local',
        'email_verified', true,
        'phone_verified', false
    ),
    'email',
    now(),
    now(),
    now()
)
on conflict (provider, provider_id) do nothing;

-- One stored conversation, so the app has history to resume on a fresh clone
-- and the Playwright suite can check that it renders without spending an
-- OpenAI turn. The assistant turn carries no result_refs: the trail ids in a
-- real answer come from whatever the local graph was ingested with, and a
-- seeded id that does not exist there would render a card pointing at nothing.
insert into conversations (id, user_id, title, created_at, updated_at)
values (
    '00000000-0000-4000-8000-000000000010',
    '00000000-0000-4000-8000-000000000001',
    'Easy trail near a lake',
    now(),
    now()
)
on conflict (id) do nothing;

insert into messages (id, conversation_id, role, content, created_at)
values
    (
        '00000000-0000-4000-8000-000000000011',
        '00000000-0000-4000-8000-000000000010',
        'user',
        'An easy trail near a lake, please.',
        now()
    ),
    (
        '00000000-0000-4000-8000-000000000012',
        '00000000-0000-4000-8000-000000000010',
        'assistant',
        'This conversation was seeded for local development, so it has no '
        || 'answer from the graph behind it. Ask something new to see a real one.',
        now()
    )
on conflict (id) do nothing;
