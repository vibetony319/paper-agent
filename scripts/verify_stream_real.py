"""Verify the streaming Markdown answer against a real model service.

Runs multi-turn requests over the SSE endpoint, checks event order, incremental
arrival, citation markers, and persistence parity with the buffered endpoint.

The provider comes from the environment, using the same variables as the
optional single-model profile (see `.env.example`):

    PAPER_AGENT_REASONING_BASE_URL   OpenAI-compatible base URL
    PAPER_AGENT_REASONING_MODEL      model name (``--model`` overrides it)
    PAPER_AGENT_REASONING_API_KEY    API key, ``EMPTY`` when the service wants none

Run from the repository root:

    ./.venv/Scripts/python.exe scripts/verify_stream_real.py --turns 4

By default it uploads the generated synthetic fixture paper. Point ``--pdf`` at
a real paper when you want the model to answer a substantive question; the
streaming contract is checked either way, while citation persistence is only
required for an answer the model itself marks as grounded.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_agent.app import create_app  # noqa: E402
from paper_agent.config import Settings  # noqa: E402
from tests.e2e.server import fixture_pdf_bytes  # noqa: E402


def resolve_provider(arguments: argparse.Namespace) -> tuple[str, str, str]:
    """Return (display_name, base_url, api_key, model) from the environment."""
    base_url = os.environ.get("PAPER_AGENT_REASONING_BASE_URL")
    api_key = os.environ.get("PAPER_AGENT_REASONING_API_KEY", "EMPTY")
    model = arguments.model or os.environ.get("PAPER_AGENT_REASONING_MODEL")
    if not base_url or not model:
        raise SystemExit(
            "Set PAPER_AGENT_REASONING_BASE_URL and PAPER_AGENT_REASONING_MODEL "
            "(or pass --model) before running this check."
        )
    return f"{model} live verify", base_url, api_key, model


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="overrides PAPER_AGENT_REASONING_MODEL")
    parser.add_argument("--pdf", default=None, help="real paper to upload instead of the fixture")
    parser.add_argument("--turns", type=int, default=4)
    arguments = parser.parse_args()

    display_name, base_url, api_key, model = resolve_provider(arguments)
    print(f"provider: {base_url} | model: {model}")

    workdir = Path(tempfile.mkdtemp())
    settings = Settings(
        data_dir=workdir / "data",
        database_url=f"sqlite:///{workdir / 'data' / 'paper-agent.db'}",
    )
    client = TestClient(create_app(settings))

    profile = client.post("/api/model-profiles", json={
        "display_name": display_name,
        "base_url": base_url,
        "model_name": model,
        "api_key": api_key,
        "enabled": True,
        "is_default": True,
    })
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]
    revision = profile.json()["revision"]
    tested = client.post(
        f"/api/model-profiles/{profile_id}/test", headers={"If-Match": str(revision)}
    )
    capabilities = tested.json().get("capabilities", {})
    print("capabilities:", {key: value for key, value in capabilities.items() if key != "checked_at"})
    if not capabilities.get("tool_calling"):
        from openai import OpenAI

        reason = "unknown provider failure"
        try:
            OpenAI(base_url=base_url, api_key=api_key).chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "reply with OK"}],
                temperature=0,
            )
        except Exception as error:  # noqa: BLE001 - report the provider state verbatim
            reason = f"{type(error).__name__}: {str(error)[:300]}"
        print()
        print("BLOCKED: the configured model cannot run the agent turn yet.")
        print("provider says:", reason)
        print("Re-run this script once the provider quota/service recovers.")
        return 2

    if arguments.pdf is None:
        pdf_name, pdf_bytes = "fixture.pdf", fixture_pdf_bytes()
    else:
        pdf_path = Path(arguments.pdf)
        pdf_name, pdf_bytes = pdf_path.name, pdf_path.read_bytes()
    uploaded = client.post(
        "/api/papers",
        files={"file": (pdf_name, pdf_bytes, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    paper_id = uploaded.json()["id"]

    def read_stream(payload):
        """Consume the SSE endpoint, returning event names, text, message, timing."""
        names: list[str] = []
        text = ""
        message = None
        error = None
        started_at = time.monotonic()
        first_delta_at = None
        with client.stream(
            "POST", f"/api/papers/{paper_id}/agent/messages/stream", json=payload
        ) as response:
            assert response.status_code == 200, response.status_code
            assert response.headers["content-type"].startswith("text/event-stream")
            frame_lines: list[str] = []
            for line in response.iter_lines():
                if line:
                    frame_lines.append(line)
                    continue
                if not frame_lines:
                    continue
                name = next(
                    (item.split(":", 1)[1].strip() for item in frame_lines if item.startswith("event:")),
                    None,
                )
                data = next(
                    (item.split(":", 1)[1].strip() for item in frame_lines if item.startswith("data:")),
                    None,
                )
                frame_lines = []
                if name is None or data is None:
                    continue
                names.append(name)
                body = json.loads(data)
                if name == "delta":
                    if first_delta_at is None:
                        first_delta_at = time.monotonic()
                    text += body["text"]
                elif name == "completed":
                    message = body["message"]
                elif name == "error":
                    error = body
        return {
            "names": names,
            "text": text,
            "message": message,
            "error": error,
            "total_seconds": time.monotonic() - started_at,
            "first_delta_seconds": None if first_delta_at is None else first_delta_at - started_at,
        }

    turns = [
        "这篇论文讲了什么？",
        "再详细解释一下论文提出的方法",
        "结论部分说了什么？",
        "实验用了哪些数据集？",
    ][: max(1, arguments.turns)]

    conversation_id = None
    failures: list[str] = []
    for index, question in enumerate(turns, start=1):
        payload = {
            "content": question,
            "model_profile_id": profile_id,
            "request_id": str(uuid.uuid4()),
        }
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        result = read_stream(payload)
        print()
        print(f"--- turn {index}: {question}")
        print("events:", result["names"])
        print(
            "first delta after %.2fs, total %.2fs"
            % (result["first_delta_seconds"] or -1, result["total_seconds"])
        )
        if result["error"] is not None:
            print("ERROR EVENT:", result["error"])
            failures.append(f"turn {index}: error event {result['error']}")
            continue
        message = result["message"]
        if message is None:
            failures.append(f"turn {index}: no completed event")
            continue
        if result["names"][0] != "started" or result["names"][-1] != "completed":
            failures.append(f"turn {index}: unexpected event order {result['names']}")
        if len(result["names"]) < 3:
            failures.append(f"turn {index}: expected multiple deltas, got {result['names']}")
        # The parser strips the optional first-line directive and splits the answer
        # at the background separator, so the streamed markdown must lead with the
        # persisted answer and end with the persisted background section.
        body = result["text"]
        if body.startswith("[[status:insufficient_evidence]]"):
            body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.strip()
        if not body.startswith(message["paper_answer"]):
            failures.append(
                f"turn {index}: streamed text does not lead with the persisted answer:"
                f" {body[:100]!r} vs {message['paper_answer'][:100]!r}"
            )
        if message["background_explanation"] is not None and not body.endswith(
            message["background_explanation"]
        ):
            failures.append(
                f"turn {index}: streamed text does not end with the background section:"
                f" {body[-100:]!r} vs {message['background_explanation'][-100:]!r}"
            )
        if message["status"] not in ("grounded", "insufficient_evidence"):
            failures.append(f"turn {index}: unexpected status {message['status']}")
        # A model that reports insufficient evidence for the uploaded paper is
        # allowed to answer without citations; a grounded answer is not.
        if message["status"] == "grounded":
            if "[[" not in message["paper_answer"]:
                failures.append(f"turn {index}: answer has no inline citation marker")
            if not message["citations"]:
                failures.append(f"turn {index}: cited markers were not persisted as citations")
        print(
            "status:", message["status"],
            "| citations:", len(message["citations"]),
            "| deltas:", len(result["names"]) - 2,
        )
        print("answer head:", message["paper_answer"][:160].replace("\n", " "))
        if message["background_explanation"] is not None:
            print("background head:", message["background_explanation"][:80].replace("\n", " "))
        conversation_id = message["conversation_id"]

    # Duplicate request replay must not regenerate the answer.
    last_payload = {
        "content": turns[-1],
        "model_profile_id": profile_id,
        "request_id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
    }
    first_run = read_stream(last_payload)
    replay = read_stream(last_payload)
    print()
    print("replay events:", replay["names"])
    if [name for name in replay["names"]] != ["started", "completed"]:
        failures.append(f"replay: unexpected event order {replay['names']}")
    elif replay["message"]["message_id"] != first_run["message"]["message_id"]:
        failures.append("replay: regenerated a different message")

    # The buffered endpoint must agree with the streamed contract.
    buffered = client.post(
        f"/api/papers/{paper_id}/agent/messages",
        json={
            "content": "用一句话概括这篇论文。",
            "model_profile_id": profile_id,
            "request_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
        },
    )
    print("buffered endpoint:", buffered.status_code)
    if buffered.status_code != 200:
        failures.append(f"buffered endpoint failed: {buffered.text[:200]}")
    else:
        body = buffered.json()
        print("buffered answer head:", body["paper_answer"][:120].replace("\n", " "))
        if body["status"] == "grounded" and "[[" not in body["paper_answer"]:
            failures.append("buffered: answer has no inline citation marker")

    history = client.get(
        f"/api/papers/{paper_id}/agent/conversations/{conversation_id}"
    ).json()
    print("durable messages:", len(history["messages"]))
    roles = [item["role"] for item in history["messages"]]
    print("durable roles:", roles)
    if not roles or roles[0] != "user":
        failures.append(f"history: does not start with a user turn {roles}")
    if any(
        roles[index - 1] != "user"
        for index, role in enumerate(roles)
        if role == "assistant"
    ):
        failures.append(f"history: assistant message without a preceding user turn {roles}")
    if roles and roles[-1] != "assistant":
        failures.append(f"history: the completed last turn stored no answer {roles}")

    print()
    if failures:
        print("FAILURES:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("ALL STREAMING TURNS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
