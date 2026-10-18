"""Versioned, provider-independent data at V2 component boundaries."""

from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CONTRACT_SCHEMA_VERSION = "1.0"

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*(?:[.:][a-z0-9_]+)*$",
    ),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Version = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
Probability = Annotated[float, Field(strict=True, ge=0, le=1)]
Milliseconds = Annotated[float, Field(strict=True, ge=0)]
SideEffect = Literal["read_only", "write", "destructive", "unknown"]
T = TypeVar("T")


class ContractModel(BaseModel):
    """Reject unknown fields, unsupported versions and non-finite measurements."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )
    schema_version: Literal["1.0"] = CONTRACT_SCHEMA_VERSION


def require_unique(values: tuple[T, ...], field: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must not contain duplicates")
