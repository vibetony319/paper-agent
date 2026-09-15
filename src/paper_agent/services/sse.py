"""Server-sent event framing shared by the streaming endpoints."""

import json


def sse_frame(event: str, payload: dict[str, object]) -> bytes:
    """Encode one server-sent event frame."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")
