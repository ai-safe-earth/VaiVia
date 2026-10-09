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

"Local" is loopback or a single-label name such as ``supabase_db_vaivia``: a
container on a shared Docker network (the laptop stage, ``docs/plans/deploy.md``
L1). Public DNS names always carry a dot, so a single label never leaves the
machine's own networks.
"""

from __future__ import annotations

from urllib.parse import urlparse

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", ""})


def _is_local(hostname: str) -> bool:
    if hostname in LOCAL_HOSTS:
        return True
    # A single DNS label: no dot (every IPv4 address and public name has one)
    # and no colon (every IPv6 address has one).
    return "." not in hostname and ":" not in hostname


def should_use_tls(connection_string: str) -> bool:
    """True for any host that is not loopback or a single-label Docker name.

    An unparseable string encrypts: failing secure costs a connection error,
    while failing open sends credentials over the public internet in plaintext.
    """
    try:
        hostname = urlparse(connection_string).hostname
    except ValueError:
        return True
    if hostname is None:
        return True
    return not _is_local(hostname.lower())


def asyncpg_ssl(connection_string: str) -> str | bool:
    """The ``ssl`` argument for ``asyncpg.connect`` / ``create_pool``.

    ``"require"`` encrypts without verifying the chain; ``False`` disables TLS
    for a local connection that does not offer it.
    """
    return "require" if should_use_tls(connection_string) else False
