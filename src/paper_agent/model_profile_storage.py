from dataclasses import replace
from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine, RowMapping

from paper_agent.database import initialize_database, model_profiles
from paper_agent.model_profiles import (
    UNCHANGED,
    ModelCapabilities,
    ModelProfile,
    ModelProfileChanges,
)


class ModelProfileRevisionError(RuntimeError):
    """Raised when an optimistic model-profile write cannot be applied."""

    def __init__(self) -> None:
        super().__init__("Model profile revision conflict.")


class ModelProfileRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: Engine = initialize_database(database_url)

    def create(self, profile: ModelProfile) -> ModelProfile:
        with self.engine.begin() as connection:
            self._insert(connection, profile)
        return profile

    def create_replacing_default(self, profile: ModelProfile) -> ModelProfile:
        if not profile.is_default:
            raise ValueError("replacement profile must be default")
        with self.engine.begin() as connection:
            self._clear_other_defaults(connection, profile.id, profile.updated_at)
            self._insert(connection, profile)
        return profile

    def get(self, profile_id: str) -> ModelProfile | None:
        with self.engine.connect() as connection:
            return self._get(connection, profile_id)

    def list_active(self) -> tuple[ModelProfile, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(model_profiles)
                .where(model_profiles.c.deleted_at.is_(None))
                .order_by(model_profiles.c.created_at.asc(), model_profiles.c.id.asc())
            ).mappings()
            return tuple(_profile_from_row(row) for row in rows)

    def update(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        changes: ModelProfileChanges,
        secret_state_changed: bool = False,
    ) -> ModelProfile:
        with self.engine.begin() as connection:
            current = self._get(connection, profile_id)
            if current is None or current.deleted_at is not None:
                raise ModelProfileRevisionError()
            updated = _apply_changes(current, changes)
            material_config_changed = (
                updated.base_url != current.base_url
                or updated.model_name != current.model_name
                or updated.secret_ref != current.secret_ref
                or secret_state_changed
            )
            now = datetime.now(UTC)
            if updated.is_default and not current.is_default:
                self._clear_other_defaults(connection, profile_id, now)
            values: dict[str, object] = {
                "display_name": updated.display_name,
                "base_url": updated.base_url,
                "model_name": updated.model_name,
                "secret_ref": updated.secret_ref,
                "context_length": updated.context_length,
                "max_output_tokens": updated.max_output_tokens,
                "enabled": updated.enabled,
                "is_default": updated.is_default,
                "revision": expected_revision + 1,
                "updated_at": _serialize_datetime(now),
            }
            if material_config_changed:
                values.update(
                    basic_chat=False,
                    structured_output=False,
                    tool_calling=False,
                    capabilities_checked_at=None,
                )
            result = connection.execute(
                update(model_profiles)
                .where(model_profiles.c.id == profile_id)
                .where(model_profiles.c.revision == expected_revision)
                .where(model_profiles.c.deleted_at.is_(None))
                .values(**values)
            )
            self._require_one_row(result.rowcount)
            return self._require_profile(connection, profile_id)

    def update_capabilities(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        capabilities: ModelCapabilities,
    ) -> ModelProfile:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(model_profiles)
                .where(model_profiles.c.id == profile_id)
                .where(model_profiles.c.revision == expected_revision)
                .where(model_profiles.c.deleted_at.is_(None))
                .values(
                    basic_chat=capabilities.basic_chat,
                    structured_output=capabilities.structured_output,
                    tool_calling=capabilities.tool_calling,
                    capabilities_checked_at=_serialize_datetime(capabilities.checked_at),
                    revision=expected_revision + 1,
                    updated_at=_serialize_datetime(now),
                )
            )
            self._require_one_row(result.rowcount)
            return self._require_profile(connection, profile_id)

    def set_default(self, profile_id: str, *, expected_revision: int) -> ModelProfile:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            self._clear_other_defaults(connection, profile_id, now)
            result = connection.execute(
                update(model_profiles)
                .where(model_profiles.c.id == profile_id)
                .where(model_profiles.c.revision == expected_revision)
                .where(model_profiles.c.deleted_at.is_(None))
                .values(
                    is_default=True,
                    revision=expected_revision + 1,
                    updated_at=_serialize_datetime(now),
                )
            )
            self._require_one_row(result.rowcount)
            return self._require_profile(connection, profile_id)

    def soft_delete(self, profile_id: str, *, expected_revision: int) -> ModelProfile:
        return self.soft_delete_and_set_default(
            profile_id,
            expected_revision=expected_revision,
            replacement_profile_id=None,
        )

    def soft_delete_and_set_default(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        replacement_profile_id: str | None,
    ) -> ModelProfile:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(model_profiles)
                .where(model_profiles.c.id == profile_id)
                .where(model_profiles.c.revision == expected_revision)
                .where(model_profiles.c.deleted_at.is_(None))
                .values(
                    is_default=False,
                    revision=expected_revision + 1,
                    updated_at=_serialize_datetime(now),
                    deleted_at=_serialize_datetime(now),
                )
            )
            self._require_one_row(result.rowcount)
            if replacement_profile_id is not None:
                replacement = connection.execute(
                    update(model_profiles)
                    .where(model_profiles.c.id == replacement_profile_id)
                    .where(model_profiles.c.enabled.is_(True))
                    .where(model_profiles.c.deleted_at.is_(None))
                    .values(
                        is_default=True,
                        revision=model_profiles.c.revision + 1,
                        updated_at=_serialize_datetime(now),
                    )
                )
                self._require_one_row(replacement.rowcount)
            return self._require_profile(connection, profile_id)

    @staticmethod
    def _insert(connection: Connection, profile: ModelProfile) -> None:
        connection.execute(
            insert(model_profiles).values(
                id=profile.id,
                display_name=profile.display_name,
                base_url=profile.base_url,
                model_name=profile.model_name,
                secret_ref=profile.secret_ref,
                context_length=profile.context_length,
                max_output_tokens=profile.max_output_tokens,
                enabled=profile.enabled,
                is_default=profile.is_default,
                revision=profile.revision,
                basic_chat=profile.capabilities.basic_chat,
                structured_output=profile.capabilities.structured_output,
                tool_calling=profile.capabilities.tool_calling,
                capabilities_checked_at=_serialize_datetime(
                    profile.capabilities.checked_at
                ),
                created_at=_serialize_datetime(profile.created_at),
                updated_at=_serialize_datetime(profile.updated_at),
                deleted_at=_serialize_datetime(profile.deleted_at),
            )
        )

    @staticmethod
    def _require_one_row(rowcount: int) -> None:
        if rowcount != 1:
            raise ModelProfileRevisionError()

    @staticmethod
    def _get(connection: Connection, profile_id: str) -> ModelProfile | None:
        row = connection.execute(
            select(model_profiles).where(model_profiles.c.id == profile_id)
        ).mappings().one_or_none()
        return None if row is None else _profile_from_row(row)

    def _require_profile(self, connection: Connection, profile_id: str) -> ModelProfile:
        profile = self._get(connection, profile_id)
        if profile is None:
            raise ModelProfileRevisionError()
        return profile

    @staticmethod
    def _clear_other_defaults(
        connection: Connection, profile_id: str, now: datetime
    ) -> None:
        connection.execute(
            update(model_profiles)
            .where(model_profiles.c.id != profile_id)
            .where(model_profiles.c.is_default.is_(True))
            .where(model_profiles.c.deleted_at.is_(None))
            .values(
                is_default=False,
                revision=model_profiles.c.revision + 1,
                updated_at=_serialize_datetime(now),
            )
        )


def _apply_changes(profile: ModelProfile, changes: ModelProfileChanges) -> ModelProfile:
    values: dict[str, object] = {}
    for field_name in ("display_name", "base_url", "model_name", "enabled", "is_default"):
        value = getattr(changes, field_name)
        if value is not None:
            values[field_name] = value
    if changes.secret_ref is not UNCHANGED:
        values["secret_ref"] = changes.secret_ref
    if changes.context_length is not UNCHANGED:
        values["context_length"] = changes.context_length
    if changes.max_output_tokens is not UNCHANGED:
        values["max_output_tokens"] = changes.max_output_tokens
    return replace(profile, **values)


def _profile_from_row(row: RowMapping | Mapping[str, object]) -> ModelProfile:
    return ModelProfile(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        base_url=str(row["base_url"]),
        model_name=str(row["model_name"]),
        secret_ref=row["secret_ref"],
        context_length=row["context_length"],
        max_output_tokens=row["max_output_tokens"],
        enabled=bool(row["enabled"]),
        is_default=bool(row["is_default"]),
        revision=int(row["revision"]),
        capabilities=ModelCapabilities(
            basic_chat=bool(row["basic_chat"]),
            structured_output=bool(row["structured_output"]),
            tool_calling=bool(row["tool_calling"]),
            checked_at=_parse_datetime(row["capabilities_checked_at"]),
        ),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
        deleted_at=_parse_datetime(row["deleted_at"]),
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))
