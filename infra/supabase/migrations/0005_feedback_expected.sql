-- The thumbs-down asks two questions (owner decision 2026-08-27): what's
-- wrong (`comment`, from 0004) and how it should be instead (`expected`).
-- Both ride the same upsert and both are eval material.
--
-- Idempotent, like every migration here: re-running is the normal case.

alter table message_feedback add column if not exists expected text;
