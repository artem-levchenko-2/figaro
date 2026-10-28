"""The text of a script's value: what `figaro exec` prints, and the bridge's `result`.

The bridge makes it for a client that wants `result`, the CLI for itself when it asks with
`value_only`, from the same function: both give the same text. Only `json` is imported here,
so the CLI loads this without loading the bridge.
"""
import json

# Indented output uses Python's pure-Python encoder (~6x slower than the C one): ~120 ms on a
# 3 MB result. Past 1 MB nobody reads it by eye anyway.
COMPACT_OVER = 1_000_000


def render(value) -> str:
    """A string as it is, a boolean as true / false, anything else as JSON: indented,
    compact past COMPACT_OVER characters."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    compact = json.dumps(value, ensure_ascii=False)
    if len(compact) > COMPACT_OVER:
        return compact
    return json.dumps(value, indent=2, ensure_ascii=False)
