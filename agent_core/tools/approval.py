"""Canonical host approval receipt; never part of a model tool schema."""

import hashlib
import json


def approval_fingerprint(tool_name, arguments):
    return hashlib.sha256(
        json.dumps(
            {"tool": tool_name, "arguments": arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
