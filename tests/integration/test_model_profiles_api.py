from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from paper_agent.app import create_app
from paper_agent.config import Settings
from paper_agent.models.vllm import VllmModelConfig
from paper_agent.model_profiles import ModelCapabilities
from paper_agent.routes.model_profiles import ModelProfileHttpError, _safe_errors
from paper_agent.services.reasoning_clients import ENVIRONMENT_FALLBACK_PROFILE_ID


PROFILE_FIELDS = {
    "id",
    "display_name",
    "base_url",
    "model_name",
    "enabled",
    "is_default",
    "revision",
    "has_api_key",
    "api_key_mask",
    "context_length",
    "max_output_tokens",
    "capabilities",
    "read_only",
}


def _exception_chain_text(error: BaseException) -> str:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    return "\n".join(messages)


def _profile_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "本地 Qwen",
        "base_url": "http://127.0.0.1:8001/v1",
        "model_name": "Qwen3-32B",
        "enabled": True,
        "is_default": False,
    }
    payload.update(changes)
    return payload


def _create_profile(client: TestClient, **changes: object) -> dict[str, object]:
    response = client.post("/api/model-profiles", json=_profile_payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def _configured_client(tmp_path: Path, *, api_key: str = "EMPTY") -> TestClient:
    data_dir = tmp_path / "configured-data"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'paper-agent.db'}",
        reasoning_model=VllmModelConfig(
            base_url="http://127.0.0.1:9/v1",
            model="environment-model",
            api_key=api_key,
        ),
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_model_profile_crud_masks_secret_and_soft_deletes(client: TestClient) -> None:
    """Breaks if CRUD exposes a key, omits fixed fields, or hard-visible deletes remain."""
    created_response = client.post(
        "/api/model-profiles",
        json=_profile_payload(api_key="secret-value", is_default=True),
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert set(created) == PROFILE_FIELDS
    assert created["has_api_key"] is True
    assert created["api_key_mask"] == "••••••••"
    assert created["read_only"] is False
    assert "api_key" not in created
    assert "secret-value" not in created_response.text
    assert client.get("/api/model-profiles").json() == [created]

    deleted = client.delete(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(created["revision"])},
    )

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/api/model-profiles").json() == []
    assert client.app.state.model_secret_store.get(
        f"model-profile:{created['id']}"
    ) == ""


def test_patch_distinguishes_omitted_key_from_explicit_clear(client: TestClient) -> None:
    """Breaks if an unrelated PATCH clears a key or explicit clear leaves secret state."""
    created = _create_profile(client, api_key="keep-until-cleared")

    renamed = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(created["revision"])},
        json={"display_name": "重命名模型"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["has_api_key"] is True
    assert (
        client.app.state.model_secret_store.get(
            f"model-profile:{created['id']}"
        )
        == "keep-until-cleared"
    )

    cleared = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(renamed.json()["revision"])},
        json={"api_key": "EMPTY"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False
    assert cleared.json()["api_key_mask"] is None
    assert client.app.state.model_secret_store.get(
        f"model-profile:{created['id']}"
    ) == ""


def test_patch_resets_capabilities_only_for_material_configuration_changes(
    client: TestClient,
) -> None:
    """Breaks if API edits retain stale probes or harmless names erase valid probes."""
    created = _create_profile(client, api_key="first-secret")
    repository = client.app.state.model_profile_repository
    tested = repository.update_capabilities(
        created["id"],
        expected_revision=created["revision"],
        capabilities=ModelCapabilities(
            basic_chat=True,
            structured_output=True,
            tool_calling=True,
        ),
    )
    secret_ref = tested.secret_ref

    renamed = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(tested.revision)},
        json={"display_name": "Renamed only"},
    )
    replaced = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(renamed.json()["revision"])},
        json={"api_key": "replacement-secret"},
    )

    assert renamed.status_code == 200
    assert renamed.json()["capabilities"] == {
        "basic_chat": True,
        "structured_output": True,
        "tool_calling": True,
        "checked_at": None,
    }
    assert replaced.status_code == 200
    assert replaced.json()["capabilities"] == {
        "basic_chat": False,
        "structured_output": False,
        "tool_calling": False,
        "checked_at": None,
    }
    current = repository.get(created["id"])
    assert current.secret_ref == secret_ref
    assert client.app.state.model_secret_store.get(secret_ref) == "replacement-secret"


def test_validation_errors_are_stable_and_never_echo_sensitive_input(
    client: TestClient,
) -> None:
    """Breaks if invalid names, URLs, fields, or oversized keys leak request content."""
    secret = "validation-secret-that-must-not-return"
    invalid_payloads = (
        _profile_payload(display_name="   "),
        _profile_payload(model_name="   "),
        _profile_payload(base_url=f"https://user:{secret}@localhost:8000/v1"),
        _profile_payload(api_key=secret * 300),
        _profile_payload(unexpected=secret),
    )

    for payload in invalid_payloads:
        response = client.post("/api/model-profiles", json=payload)
        assert response.status_code == 422
        assert response.json() == {
            "code": "validation_error",
            "detail": "模型档案请求无效。",
        }
        assert secret not in response.text


def test_revisioned_routes_require_if_match_and_reject_stale_revisions(
    client: TestClient,
) -> None:
    """Breaks if missing, malformed, or stale revisions can mutate a profile."""
    created = _create_profile(client)
    path = f"/api/model-profiles/{created['id']}"

    missing = client.patch(path, json={"display_name": "No precondition"})
    malformed = client.delete(path, headers={"If-Match": "not-a-revision"})
    assert missing.status_code == 428
    assert missing.json() == {
        "code": "if_match_required",
        "detail": "缺少 If-Match 修订号。",
    }
    assert malformed.status_code == 400
    assert malformed.json() == {
        "code": "invalid_if_match",
        "detail": "If-Match 必须是正整数修订号。",
    }

    updated = client.patch(
        path,
        headers={"If-Match": str(created["revision"])},
        json={"display_name": "Current"},
    )
    assert updated.status_code == 200
    stale = client.post(
        f"{path}/default",
        headers={"If-Match": str(created["revision"])},
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "revision_conflict",
        "detail": "模型档案已被其他操作修改，请刷新后重试。",
    }
    assert "Current" in client.get("/api/model-profiles").text


def test_model_profile_error_translation_drops_raw_exception_chain() -> None:
    """Breaks if a stable HTTP error retains provider, database, or secret text."""
    raw_secret = "raw-storage-api-key-secret"

    def fail() -> None:
        raise RuntimeError(raw_secret)

    with pytest.raises(ModelProfileHttpError) as caught:
        _safe_errors(fail)

    error = caught.value
    assert error.status_code == 500
    assert error.code == "model_profile_error"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert raw_secret not in _exception_chain_text(error)


def test_arbitrarily_long_if_match_is_a_stable_invalid_header(
    client: TestClient,
) -> None:
    """Breaks if integer conversion limits turn hostile revision text into 422/500."""
    created = _create_profile(client)

    response = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": "9" * 5000},
        json={"display_name": "Must not update"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "invalid_if_match",
        "detail": "If-Match 必须是正整数修订号。",
    }
    assert client.get("/api/model-profiles").json()[0]["revision"] == created[
        "revision"
    ]


def test_stale_capability_test_wins_over_missing_credentials(
    client: TestClient,
) -> None:
    """Breaks if a stale test resolves credentials before enforcing its revision CAS."""
    created = _create_profile(client, api_key="removed-before-stale-test")
    updated = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(created["revision"])},
        json={"display_name": "Newer revision"},
    )
    assert updated.status_code == 200
    client.app.state.model_secret_store.delete(
        f"model-profile:{created['id']}"
    )

    stale = client.post(
        f"/api/model-profiles/{created['id']}/test",
        headers={"If-Match": str(created["revision"])},
    )

    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"


def test_default_switch_and_delete_promote_earliest_remaining_enabled_profile(
    client: TestClient,
) -> None:
    """Breaks if deletion promotes a disabled/later profile or leaves a valid set defaultless."""
    earliest = _create_profile(client, display_name="Earliest")
    disabled = _create_profile(client, display_name="Disabled", enabled=False)
    later = _create_profile(client, display_name="Later")
    selected_response = client.post(
        f"/api/model-profiles/{later['id']}/default",
        headers={"If-Match": str(later["revision"])},
    )
    assert selected_response.status_code == 200
    selected = selected_response.json()
    assert selected["is_default"] is True

    deleted = client.delete(
        f"/api/model-profiles/{later['id']}",
        headers={"If-Match": str(selected["revision"])},
    )
    assert deleted.status_code == 204
    listed = client.get("/api/model-profiles").json()
    by_name = {profile["display_name"]: profile for profile in listed}
    assert by_name["Earliest"]["is_default"] is True
    assert by_name["Disabled"]["is_default"] is False
    assert by_name["Disabled"]["enabled"] is False

    earliest_current = by_name["Earliest"]
    assert client.delete(
        f"/api/model-profiles/{earliest_current['id']}",
        headers={"If-Match": str(earliest_current["revision"])},
    ).status_code == 204
    remaining = client.get("/api/model-profiles").json()
    assert len(remaining) == 1
    assert remaining[0]["enabled"] is False
    assert remaining[0]["is_default"] is False


def test_environment_fallback_listing_masking_and_read_only_conflicts(
    tmp_path: Path,
) -> None:
    """Breaks if fallback visibility ignores enabled DB profiles or permits mutations."""
    client = _configured_client(tmp_path, api_key="fallback-secret")
    try:
        fallback = client.get("/api/model-profiles").json()
        assert len(fallback) == 1
        fallback_profile = fallback[0]
        assert fallback_profile["id"] == ENVIRONMENT_FALLBACK_PROFILE_ID
        assert fallback_profile["read_only"] is True
        assert fallback_profile["has_api_key"] is True
        assert fallback_profile["api_key_mask"] == "••••••••"
        assert "fallback-secret" not in str(fallback_profile)

        disabled = _create_profile(client, display_name="Disabled", enabled=False)
        listed = client.get("/api/model-profiles").json()
        assert {profile["id"] for profile in listed} == {
            ENVIRONMENT_FALLBACK_PROFILE_ID,
            disabled["id"],
        }

        for method, suffix, body in (
            ("patch", "", {"display_name": "不可修改"}),
            ("delete", "", None),
            ("post", "/default", None),
        ):
            response = client.request(
                method,
                f"/api/model-profiles/{ENVIRONMENT_FALLBACK_PROFILE_ID}{suffix}",
                headers={"If-Match": str(fallback_profile["revision"])},
                json=body,
            )
            assert response.status_code == 409
            assert response.json() == {
                "code": "profile_read_only",
                "detail": "环境变量模型档案为只读，不能修改。",
            }

        enabled = _create_profile(client, display_name="Enabled")
        assert {profile["id"] for profile in client.get("/api/model-profiles").json()} == {
            disabled["id"],
            enabled["id"],
        }
    finally:
        client.close()


class _CapabilityOpenAI:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: object) -> object:
        if "response_format" in kwargs:
            content = '{"status":"ok"}'
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
        if "tools" in kwargs:
            tool_call = SimpleNamespace(
                id="health-1",
                function=SimpleNamespace(
                    name="paper_agent_tool_health", arguments="{}"
                ),
            )
            message = SimpleNamespace(content=None, tool_calls=[tool_call])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
        message = SimpleNamespace(content="OK")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_connection_test_uses_head_without_model_generation_or_profile_mutation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection check does not spend model tokens or expose credentials."""
    created = _create_profile(client, api_key="capability-secret")
    calls: list[tuple[str, float, bool]] = []

    def respond(url: str, *, timeout: float, follow_redirects: bool):
        calls.append((url, timeout, follow_redirects))
        return SimpleNamespace(status_code=405)

    monkeypatch.setattr("paper_agent.services.model_profiles.httpx.head", respond)

    response = client.post(
        f"/api/model-profiles/{created['id']}/test",
        headers={"If-Match": str(created["revision"])},
    )

    assert response.status_code == 200
    assert response.json() == {"reachable": True, "http_status": 405}
    assert calls == [(created["base_url"], 3.0, False)]
    assert client.get("/api/model-profiles").json() == [created]
    assert "capability-secret" not in response.text


def test_connection_failure_is_fast_and_safe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    created = _create_profile(client)

    def fail(_url: str, *, timeout: float, follow_redirects: bool):
        assert timeout == 3.0
        assert follow_redirects is False
        raise httpx.ConnectError("private transport detail")

    monkeypatch.setattr("paper_agent.services.model_profiles.httpx.head", fail)
    response = client.post(
        f"/api/model-profiles/{created['id']}/test",
        headers={"If-Match": str(created["revision"])},
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "connection_failed",
        "detail": "无法连接模型服务地址。",
    }
    assert "private transport detail" not in response.text


def test_openapi_response_and_application_wiring_are_secret_safe(
    client: TestClient,
) -> None:
    """Breaks if dependency assembly is incomplete or response schemas permit key write-back."""
    state = client.app.state
    assert state.model_profile_repository is not None
    assert state.model_secret_store is not None
    assert state.model_profile_service is not None
    assert state.reasoning_client_provider is not None
    assert not hasattr(state.paper_agent_runtime, "client")

    openapi = client.get("/openapi.json").json()
    response_schema = openapi["components"]["schemas"]["ModelProfileResponse"]
    assert set(response_schema["properties"]) == PROFILE_FIELDS
    assert "api_key" not in response_schema["properties"]
    assert openapi["paths"]["/api/model-profiles"]["get"]
    assert openapi["paths"]["/api/model-profiles/{profile_id}/default"]["post"]


def test_environment_fallback_supplies_compatibility_clients_through_provider(
    tmp_path: Path,
) -> None:
    """Breaks if app wiring constructs separate fixed Agent clients."""
    client = _configured_client(tmp_path)
    try:
        resolved = client.app.state.reasoning_client_provider.resolve(
            ENVIRONMENT_FALLBACK_PROFILE_ID
        )
        assert not hasattr(client.app.state.paper_agent_runtime, "client")
        assert resolved.structured is not None
    finally:
        client.close()


def test_token_limit_fields_roundtrip_and_clear(client: TestClient) -> None:
    """Breaks if token limits fail to persist, update, or clear to unlimited."""
    created = _create_profile(client, context_length=131_072, max_output_tokens=8_192)
    assert created["context_length"] == 131_072
    assert created["max_output_tokens"] == 8_192

    updated = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(created["revision"])},
        json={"max_output_tokens": 16_384},
    )
    assert updated.status_code == 200
    assert updated.json()["context_length"] == 131_072
    assert updated.json()["max_output_tokens"] == 16_384

    # An unrelated PATCH must not disturb the limits, and a later PATCH can
    # clear both back to "no limit, no compaction".
    renamed = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(updated.json()["revision"])},
        json={"display_name": "重命名模型"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["context_length"] == 131_072
    assert renamed.json()["max_output_tokens"] == 16_384

    cleared = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(renamed.json()["revision"])},
        json={"context_length": None, "max_output_tokens": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["context_length"] is None
    assert cleared.json()["max_output_tokens"] is None
    assert client.get("/api/model-profiles").json() == [cleared.json()]


def test_token_limit_validation_rejects_out_of_range_and_reversed_pairs(
    client: TestClient,
) -> None:
    """Breaks if absurd limits or an output cap above the context window pass."""
    invalid_payloads = (
        _profile_payload(context_length=999),
        _profile_payload(context_length=10_000_001),
        _profile_payload(max_output_tokens=0),
        _profile_payload(max_output_tokens=200_001),
        _profile_payload(context_length=2_048, max_output_tokens=2_048),
        _profile_payload(context_length=2_048, max_output_tokens=4_096),
    )

    for payload in invalid_payloads:
        response = client.post("/api/model-profiles", json=payload)
        assert response.status_code == 422
        assert response.json() == {
            "code": "validation_error",
            "detail": "模型档案请求无效。",
        }


def test_patch_token_limit_validation_rejects_shrinking_below_the_output_cap(
    client: TestClient,
) -> None:
    created = _create_profile(client, context_length=131_072, max_output_tokens=8_192)

    response = client.patch(
        f"/api/model-profiles/{created['id']}",
        headers={"If-Match": str(created["revision"])},
        json={"context_length": 4_096},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "detail": "模型档案请求无效。",
    }
    # The rejected PATCH must leave the stored limits untouched.
    listed = client.get("/api/model-profiles").json()
    assert listed[0]["context_length"] == 131_072
    assert listed[0]["max_output_tokens"] == 8_192
