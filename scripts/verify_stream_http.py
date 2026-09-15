"""Verify the streamed answer over a real HTTP stack, with no external service.

Boots the paper-agent app and an OpenAI-compatible stub model server as two
uvicorn processes, then drives the SSE endpoint with a real HTTP client. This
is what the in-process TestClient cannot show: whether deltas actually reach
the client incrementally instead of arriving in one buffered burst.

Run from the repository root:

    ./.venv/Scripts/python.exe scripts/verify_stream_http.py
"""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_agent.app import create_app  # noqa: E402
from paper_agent.config import Settings  # noqa: E402
from tests.e2e.server import fixture_pdf_bytes  # noqa: E402


ANSWER = "论文提出路由负载均衡损失来分配专家工作。"
BACKGROUND = "稀疏专家路由是常见做法。"


def _evidence_from(messages: list[dict[str, Any]]) -> list[str]:
    """Collect evidence IDs from tool results and from seeded system evidence."""
    evidence: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        if message.get("role") == "tool":
            evidence.extend(json.loads(content)["evidence_element_ids"])
        elif message.get("role") == "system" and content.lstrip().startswith("{"):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            evidence.extend(payload.get("evidence_element_ids", []))
    return evidence


def _markdown_answer(messages: list[dict[str, Any]]) -> str:
    evidence = _evidence_from(messages)
    body = ANSWER + ("".join(f" [[{element_id}]]" for element_id in evidence[:1]))
    return f"{body}\n\n---\n\n{BACKGROUND}" if evidence else body


def _chunk(content: str) -> str:
    payload = {
        "id": "chunk-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "stub-model",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_stub_model_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        messages = body["messages"]
        tools = body.get("tools")

        if tools is not None:
            if tools[0]["function"]["name"] == "paper_agent_tool_health":
                call = {"id": "health-1", "type": "function", "function": {
                    "name": "paper_agent_tool_health", "arguments": "{}"}}
            elif not any(message.get("role") == "tool" for message in messages):
                call = {"id": "call-1", "type": "function", "function": {
                    "name": "search_paper",
                    "arguments": json.dumps({"query": "the", "limit": 5})}}
            else:
                return JSONResponse(_completion(None, []))
            return JSONResponse(_completion(None, [call]))

        response_format = body.get("response_format")
        if response_format is not None:
            return JSONResponse(_completion('{"status": "ok"}', []))

        if body.get("stream"):
            def deltas():
                text = _markdown_answer(messages)
                for index in range(0, len(text), 6):
                    yield _chunk(text[index:index + 6])
                    time.sleep(0.05)
                yield "data: [DONE]\n\n"

            return StreamingResponse(deltas(), media_type="text/event-stream")

        # The capability probe asks for exactly "OK" before any tool history exists.
        if not _evidence_from(messages) and messages == [{"role": "user", "content": "Return exactly OK."}]:
            return JSONResponse(_completion("OK", []))

        return JSONResponse(_completion(_markdown_answer(messages), []))

    return app


def _completion(content: str | None, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "completion-1",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-model",
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "tool_calls" if tool_calls else "stop",
        }],
    }


def serve(app: FastAPI) -> tuple[str, uvicorn.Server]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}", server


def main() -> int:
    stub_base, stub_server = serve(build_stub_model_app())

    workdir = Path(tempfile.mkdtemp())
    base_url, paper_server = serve(create_app(Settings(
        data_dir=workdir / "data",
        database_url=f"sqlite:///{workdir / 'data' / 'paper-agent.db'}",
    )))
    print("stub model:", stub_base, "| paper agent:", base_url)

    failures: list[str] = []
    try:
        with httpx.Client(base_url=base_url, timeout=120) as client:
            profile = client.post("/api/model-profiles", json={
                "display_name": "local stub",
                "base_url": f"{stub_base}/v1",
                "model_name": "stub-model",
                "api_key": "EMPTY",
                "enabled": True,
                "is_default": True,
            })
            assert profile.status_code == 201, profile.text
            profile_id = profile.json()["id"]
            revision = profile.json()["revision"]
            tested = client.post(
                f"/api/model-profiles/{profile_id}/test", headers={"If-Match": str(revision)}
            )
            capabilities = tested.json()["capabilities"]
            print("capabilities:", {k: v for k, v in capabilities.items() if k != "checked_at"})
            if not all(capabilities[key] for key in ("basic_chat", "structured_output", "tool_calling")):
                failures.append(f"capability probe failed: {capabilities}")

            uploaded = client.post(
                "/api/papers",
                files={"file": ("verify.pdf", fixture_pdf_bytes(), "application/pdf")},
            )
            assert uploaded.status_code == 201, uploaded.text[:300]
            paper_id = uploaded.json()["id"]

            def stream_turn(payload: dict[str, Any]) -> dict[str, Any]:
                names: list[str] = []
                text = ""
                message = None
                error = None
                delta_times: list[float] = []
                started_at = time.monotonic()
                with client.stream(
                    "POST", f"/api/papers/{paper_id}/agent/messages/stream", json=payload
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        return {"names": names, "error": {"code": str(response.status_code),
                                                          "detail": response.text[:200]},
                                "total": 0.0, "delta_times": delta_times}
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        failures.append("stream endpoint did not return text/event-stream")
                    frame: list[str] = []
                    for line in response.iter_lines():
                        if line:
                            frame.append(line)
                            continue
                        if not frame:
                            continue
                        name = next((item.split(":", 1)[1].strip() for item in frame if item.startswith("event:")), None)
                        data = next((item.split(":", 1)[1].strip() for item in frame if item.startswith("data:")), None)
                        frame = []
                        if name is None or data is None:
                            continue
                        names.append(name)
                        body = json.loads(data)
                        if name == "delta":
                            delta_times.append(time.monotonic() - started_at)
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
                    "total": time.monotonic() - started_at,
                    "delta_times": delta_times,
                }

            conversation_id = None
            turn_request_ids = [str(uuid.uuid4()) for _ in range(3)]
            for index, question in enumerate(
                ["这篇论文讲了什么？", "再详细解释一下方法", "结论说了什么？"], start=1
            ):
                payload = {
                    "content": question,
                    "model_profile_id": profile_id,
                    "request_id": turn_request_ids[index - 1],
                }
                if conversation_id is not None:
                    payload["conversation_id"] = conversation_id
                result = stream_turn(payload)
                print()
                print(f"--- turn {index}: {question}")
                print("events:", result["names"][:6], "..." if len(result["names"]) > 6 else "")
                if result["error"] is not None:
                    failures.append(f"turn {index}: error event {result['error']}")
                    continue
                message = result["message"]
                spread = (result["delta_times"][-1] - result["delta_times"][0]) if len(result["delta_times"]) > 1 else 0
                print(
                    "deltas: %d | first at %.3fs | spread %.3fs | total %.3fs"
                    % (len(result["delta_times"]), result["delta_times"][0], spread, result["total"])
                )
                if result["names"][0] != "started" or result["names"][-1] != "completed":
                    failures.append(f"turn {index}: event order {result['names']}")
                if len(result["delta_times"]) < 3:
                    failures.append(f"turn {index}: expected incremental deltas, got {len(result['delta_times'])}")
                if spread <= 0:
                    failures.append(f"turn {index}: deltas arrived in one buffered burst")
                expected_text = message["paper_answer"]
                if message["background_explanation"] is not None:
                    expected_text += "\n\n---\n\n" + message["background_explanation"]
                if result["text"] != expected_text:
                    failures.append(
                        f"turn {index}: streamed text != parsed answer + background:"
                        f" {result['text']!r} vs {expected_text!r}"
                    )
                if message["status"] != "grounded" or not message["citations"]:
                    failures.append(f"turn {index}: status/citations {message['status']} {message['citations']}")
                if "[[" not in message["paper_answer"]:
                    failures.append(f"turn {index}: no inline citation marker")
                if message["background_explanation"] != BACKGROUND:
                    failures.append(f"turn {index}: background split failed: {message['background_explanation']}")
                print("status:", message["status"], "| citations:", len(message["citations"]))
                print("answer:", message["paper_answer"].replace("\n", " ")[:120])
                conversation_id = message["conversation_id"]

            buffered_payload = {
                "content": "用一句话概括。",
                "model_profile_id": profile_id,
                "request_id": str(uuid.uuid4()),
            }
            if conversation_id is not None:
                buffered_payload["conversation_id"] = conversation_id
            buffered = client.post(
                f"/api/papers/{paper_id}/agent/messages", json=buffered_payload
            )
            print()
            print("buffered endpoint:", buffered.status_code, buffered.text[:200] if buffered.status_code != 200 else "")
            if buffered.status_code != 200:
                failures.append(f"buffered endpoint failed: {buffered.text[:200]}")

            history = client.get(
                f"/api/papers/{paper_id}/agent/conversations/{conversation_id}"
            ).json()
            roles = [item["role"] for item in history["messages"]]
            print("durable roles:", roles)
            if roles != ["user", "assistant"] * (len(roles) // 2):
                failures.append(f"history: unexpected role order {roles}")

            with client.stream(
                "POST",
                f"/api/papers/{paper_id}/agent/messages/stream",
                json={
                    "content": "这篇论文讲了什么？",
                    "model_profile_id": profile_id,
                    "request_id": turn_request_ids[0],
                },
            ) as replay:
                replay_names = [
                    line.split(":", 1)[1].strip()
                    for line in replay.iter_lines()
                    if line.startswith("event:")
                ]
            print("replay events:", replay_names)
            if replay_names != ["started", "completed"]:
                failures.append(f"replay: unexpected event order {replay_names}")
    finally:
        stub_server.should_exit = True
        paper_server.should_exit = True

    print()
    if failures:
        print("FAILURES:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("STREAMING VERIFICATION PASSED (real HTTP, incremental deltas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
