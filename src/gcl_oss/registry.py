from __future__ import annotations

from collections.abc import Iterable

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gcl_oss.contracts import ACTION_NAMESPACE_PATTERN, Candidate, Consequence


class ActionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(pattern=ACTION_NAMESPACE_PATTERN)
    parameter_schema: dict
    allowed_consequences: frozenset[Consequence] = Field(min_length=1)
    required_falsification_checks: tuple[str, ...] = Field(min_length=1)

    @field_validator("parameter_schema")
    @classmethod
    def valid_json_schema(cls, value: dict) -> dict:
        Draft202012Validator.check_schema(value)
        return value

    @field_validator("required_falsification_checks")
    @classmethod
    def unique_checks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("required falsification checks must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("required falsification check ids cannot be empty")
        return value


class ActionRegistry:
    def __init__(self, definitions: Iterable[ActionDefinition] = ()) -> None:
        self._definitions: dict[str, ActionDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ActionDefinition) -> None:
        if definition.action in self._definitions:
            raise ValueError(f"action is already registered: {definition.action}")
        self._definitions[definition.action] = definition

    def definition_for(self, action: str) -> ActionDefinition:
        try:
            return self._definitions[action]
        except KeyError as exc:
            raise ValueError(f"action is not registered: {action}") from exc

    def validate(self, candidate: Candidate) -> ActionDefinition:
        definition = self.definition_for(candidate.action)
        if candidate.consequence not in definition.allowed_consequences:
            raise ValueError(
                f"consequence {candidate.consequence.value!r} is not allowed for {candidate.action}"
            )
        errors = sorted(
            Draft202012Validator(definition.parameter_schema).iter_errors(candidate.parameters),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ValueError(
                f"parameters for {candidate.action} do not match the registered schema: "
                f"{errors[0].message}"
            )
        return definition

    def actions(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))
