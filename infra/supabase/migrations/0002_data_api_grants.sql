-- Grant the Data API roles the table access that RLS then filters.
--
-- 0001 enabled RLS and wrote one select policy per table, which is what makes
-- the browser's direct reads safe. It never granted the underlying privilege,
-- and on the hosted project it did not have to: entities created in `public`
-- were auto-exposed to anon/authenticated/service_role. That default is gone.
-- New projects revoke by default, and the CLI's `auto_expose_new_tables`
-- escape hatch is removed on 2026-10-30. Without these grants the browser gets
--
--     42501  permission denied for table conversations
--
-- which reads like a policy problem and is not one: a policy can only narrow a
-- privilege that exists.
--
-- SELECT only, and only for reads the browser makes under its own identity.
-- Every write still goes through the backend, which connects as the owner and
-- bypasses both layers.
--
-- `anon` is granted alongside `authenticated` deliberately. The policies are
-- written as `auth.uid() = user_id`, so an anonymous caller matches no row and
-- reads zero of them -- the behaviour verified against the hosted project. The
-- confidentiality here is RLS's job; the grant only decides whether the table
-- is addressable at all.

grant select on table conversations to anon, authenticated;
grant select on table messages      to anon, authenticated;
grant select on table usage_ledger  to anon, authenticated;
grant select on table daily_quotas  to anon, authenticated;
