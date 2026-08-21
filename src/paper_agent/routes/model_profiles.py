from typing import Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response

from paper_agent.model_profile_storage import ModelProfileRevisionError
from paper_agent.model_profiles import UNCHANGED, ModelProfileChanges
from paper_agent.schemas import (
    ModelProfileCreateRequest,
    ModelProfileErrorResponse,
    ModelProfilePatchRequest,
    ModelProfileResponse,
)
from paper_agent.services.model_profiles import (
    ModelProfileInputError,
    ModelProfileNotFoundError,
    ModelProfileReadOnlyError,
    ModelProfileService,
)
from paper_agent.services.model_secrets import ModelSecretStoreError
from paper_agent.services.reasoning_clients import ReasoningClientResolutionError


router = APIRouter(prefix="/api/model-profiles", tags=["model-profiles"])
_Result = TypeVar("_Result")
_MAX_REVISION = 9_223_372_036_854_775_807
_MAX_REVISION_DIGITS = len(str(_MAX_REVISION))
_ERROR_RESPONSES = {
    400: {"model": ModelProfileErrorResponse},
    404: {"model": ModelProfileErrorResponse},
    409: {"model": ModelProfileErrorResponse},
    422: {"model": ModelProfileErrorResponse},
    428: {"model": ModelProfileErrorResponse},
    500: {"model": ModelProfileErrorResponse},
    503: {"model": ModelProfileErrorResponse},
}


class ModelProfileHttpError(Exception):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _service(request: Request) -> ModelProfileService:
    return request.app.state.model_profile_service


def _revision(if_match: str | None) -> int:
    if if_match is None:
        raise ModelProfileHttpError(
            428, "if_match_required", "缺少 If-Match 修订号。"
        )
    if (
        len(if_match) > _MAX_REVISION_DIGITS
        or not if_match.isascii()
        or not if_match.isdecimal()
    ):
        raise ModelProfileHttpError(
            400, "invalid_if_match", "If-Match 必须是正整数修订号。"
        )
    revision = int(if_match)
    if revision < 1 or revision > _MAX_REVISION:
        raise ModelProfileHttpError(
            400, "invalid_if_match", "If-Match 必须是正整数修订号。"
        )
    return revision


def _safe_errors(action: Callable[[], _Result]) -> _Result:
    caught_error: Exception | None = None
    try:
        return action()
    except Exception as error:
        caught_error = error
    if caught_error is not None:
        raise _safe_http_error(caught_error) from None
    raise RuntimeError("unreachable")


def _safe_http_error(error: Exception) -> ModelProfileHttpError:
    if isinstance(error, ModelProfileHttpError):
        return ModelProfileHttpError(error.status_code, error.code, error.detail)
    if isinstance(error, ModelProfileNotFoundError):
        return ModelProfileHttpError(404, "profile_not_found", "模型档案不存在。")
    if isinstance(error, ModelProfileReadOnlyError):
        return ModelProfileHttpError(
            409,
            "profile_read_only",
            "环境变量模型档案为只读，不能修改。",
        )
    if isinstance(error, ModelProfileRevisionError):
        return ModelProfileHttpError(
            409,
            "revision_conflict",
            "模型档案已被其他操作修改，请刷新后重试。",
        )
    if isinstance(error, ModelProfileInputError):
        return ModelProfileHttpError(
            422, "validation_error", "模型档案请求无效。"
        )
    if isinstance(error, ModelSecretStoreError):
        return ModelProfileHttpError(
            503, "secret_store_unavailable", "模型密钥存储暂不可用。"
        )
    if isinstance(error, ReasoningClientResolutionError):
        return ModelProfileHttpError(
            503, "model_unavailable", "模型服务暂不可用。"
        )
    if isinstance(error, ValueError):
        return ModelProfileHttpError(
            422, "validation_error", "模型档案请求无效。"
        )
    return ModelProfileHttpError(
        500, "model_profile_error", "模型档案操作失败。"
    )


def _response(action: Callable[[], object]) -> ModelProfileResponse:
    return _safe_errors(lambda: ModelProfileResponse.from_view(action()))


@router.get(
    "",
    response_model=list[ModelProfileResponse],
    responses=_ERROR_RESPONSES,
)
def list_model_profiles(request: Request) -> list[ModelProfileResponse]:
    return _safe_errors(
        lambda: [
            ModelProfileResponse.from_view(view)
            for view in _service(request).list_profiles()
        ]
    )


@router.post(
    "",
    response_model=ModelProfileResponse,
    status_code=201,
    responses=_ERROR_RESPONSES,
)
def create_model_profile(
    payload: ModelProfileCreateRequest, request: Request
) -> ModelProfileResponse:
    return _response(
        lambda: _service(request).create_profile(
            display_name=payload.display_name,
            base_url=str(payload.base_url),
            model_name=payload.model_name,
            api_key=payload.api_key,
            enabled=payload.enabled,
            is_default=payload.is_default,
        )
    )


@router.patch(
    "/{profile_id}",
    response_model=ModelProfileResponse,
    responses=_ERROR_RESPONSES,
)
def update_model_profile(
    profile_id: UUID,
    payload: ModelProfilePatchRequest,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ModelProfileResponse:
    fields = payload.model_fields_set
    changes = ModelProfileChanges(
        display_name=payload.display_name if "display_name" in fields else None,
        base_url=str(payload.base_url) if "base_url" in fields else None,
        model_name=payload.model_name if "model_name" in fields else None,
        enabled=payload.enabled if "enabled" in fields else None,
        is_default=payload.is_default if "is_default" in fields else None,
    )
    api_key = payload.api_key if "api_key" in fields else UNCHANGED
    return _response(
        lambda: _service(request).update_profile(
            str(profile_id),
            expected_revision=_revision(if_match),
            changes=changes,
            api_key=api_key,
        )
    )


@router.delete(
    "/{profile_id}",
    status_code=204,
    responses=_ERROR_RESPONSES,
)
def delete_model_profile(
    profile_id: UUID,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    def delete() -> None:
        _service(request).delete_profile(
            str(profile_id), expected_revision=_revision(if_match)
        )

    _safe_errors(delete)
    return Response(status_code=204)


@router.post(
    "/{profile_id}/default",
    response_model=ModelProfileResponse,
    responses=_ERROR_RESPONSES,
)
def set_default_model_profile(
    profile_id: UUID,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ModelProfileResponse:
    return _response(
        lambda: _service(request).set_default(
            str(profile_id), expected_revision=_revision(if_match)
        )
    )


@router.post(
    "/{profile_id}/test",
    response_model=ModelProfileResponse,
    responses=_ERROR_RESPONSES,
)
def test_model_profile(
    profile_id: UUID,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ModelProfileResponse:
    return _response(
        lambda: _service(request).test_profile(
            str(profile_id), expected_revision=_revision(if_match)
        )
    )
