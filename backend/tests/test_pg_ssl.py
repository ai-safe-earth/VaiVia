"""TLS is decided by host, not by what the connection string happens to say."""

from core.pg import asyncpg_ssl, should_use_tls

HOSTED = (
    "postgresql://postgres.abc:pw@aws-1-eu-west-1.pooler.supabase.com:5432/postgres"
)
LOCAL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


def test_hosted_supabase_encrypts() -> None:
    assert should_use_tls(HOSTED) is True
    assert asyncpg_ssl(HOSTED) == "require"


def test_local_stack_does_not() -> None:
    # `supabase start` speaks no TLS, so demanding it cannot connect at all.
    for url in (LOCAL, "postgresql://postgres:postgres@localhost:54322/postgres"):
        assert should_use_tls(url) is False
        assert asyncpg_ssl(url) is False


def test_sslmode_in_the_url_does_not_decide() -> None:
    # The whole point: a hand-edited string cannot downgrade a remote link...
    assert should_use_tls(HOSTED + "?sslmode=disable") is True
    # ...nor force TLS onto a local stack that does not offer it.
    assert should_use_tls(LOCAL + "?sslmode=require") is False


def test_unparseable_fails_secure() -> None:
    # A connection error is cheaper than credentials in plaintext.
    assert should_use_tls("not a connection string") is True
    assert should_use_tls("") is True
