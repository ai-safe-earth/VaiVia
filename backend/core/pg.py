"""How a Postgres connection decides whether to encrypt.

The mirror of ``shouldUseTls`` in ``gateway/src/quotaStore.ts``, and it exists
for the same reason: the decision belongs in code, not in an ``sslmode``
parameter a hand-edited connection string can silently drop.

Two facts make the naive spellings wrong in opposite directions. Supabase
terminates TLS at the pooler with a certificate that does not chain to a public
root, so a verifying client rejects it as self-signed -- which is why this asks
for encryption without demanding a verifiable chain. And a local stack
(``supabase start``) speaks no TLS at all, so demanding it there fails to
connect. Deciding by host covers both.
"""

from __future__ import annotations

from urllib.parse import urlparse

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", ""})


def should_use_tls(connection_string: str) -> bool:
    """True for any host that is not loopback.

    An unparseable string encrypts: failing secure costs a connection error,
    while failing open sends credentials over the public internet in plaintext.
    """
    try:
        hostname = urlparse(connection_string).hostname
    except ValueError:
        return True
    if hostname is None:
        return True
    return hostname.lower() not in LOCAL_HOSTS


def asyncpg_ssl(connection_string: str) -> str | bool:
    """The ``ssl`` argument for ``asyncpg.connect`` / ``create_pool``.

    ``"require"`` encrypts without verifying the chain; ``False`` disables TLS
    for a loopback connection that does not offer it.
    """
    return "require" if should_use_tls(connection_string) else False
