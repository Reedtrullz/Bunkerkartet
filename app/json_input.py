"""Bounded, unambiguous JSON input shared by file and HTTP ingestion."""
import json


def decode_json_strict(raw: bytes, *, max_bytes: int, max_depth: int = 64) -> object:
    if len(raw) > max_bytes:
        raise ValueError("JSON input exceeds byte limit")
    text = raw.decode("utf-8")
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise ValueError("JSON input exceeds nesting limit")
        elif char in "]}":
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON contains duplicate object keys")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("JSON contains a nonstandard number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
