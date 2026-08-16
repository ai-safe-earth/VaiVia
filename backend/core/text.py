"""Small text helpers shared across layers."""

# Every character Lucene treats as query syntax. User-supplied place names are
# search terms, never operators, so all of these are escaped before the string
# reaches db.index.fulltext.queryNodes.
_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def lucene_escape(text: str) -> str:
    """Escape Lucene query syntax so free text is only ever search terms."""
    return "".join(f"\\{c}" if c in _LUCENE_SPECIAL else c for c in text)
