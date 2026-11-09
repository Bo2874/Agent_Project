"""Google declarations, without credentials or backend imports in the sandbox."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from artifact_runtime.base import StrictModel


class GoogleFileRef(StrictModel):
    connection_id: UUID
    file_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    context_id: UUID


class PythonGoogleInput(StrictModel):
    ref: GoogleFileRef
    mode: Literal["native", "download", "export"] = "native"
    range_a1: str | None = Field(default=None, min_length=1, max_length=160)
    export_mime: (
        Literal[
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/pdf",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def matching_mode(self):
        if (self.mode == "export") != (self.export_mime is not None):
            raise ValueError("Export requires an explicit supported export_mime")
        if self.range_a1 is not None and self.mode != "native":
            raise ValueError("Only native Sheets input accepts a range")
        return self


class PythonGoogleUpload(StrictModel):
    kind: Literal["upload"]
    connection_id: UUID
    output_path: str = Field(min_length=1, max_length=240)
    mode: Literal["keep_format", "google_native"]
    input_index: int | None = Field(default=None, ge=0, le=2, strict=True)


class PythonGoogleUpdate(StrictModel):
    kind: Literal["batch_update"]
    input_index: int = Field(ge=0, le=2, strict=True)
    request_path: str = Field(min_length=1, max_length=240)


PythonGoogleAction = Annotated[PythonGoogleUpload | PythonGoogleUpdate, Field(discriminator="kind")]
