"""JSON-only spreadsheet operations; safe to validate in the web process."""

import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    Field,
    FiniteFloat,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from artifact_runtime.base import StrictModel


def sheet_name(value: str) -> str:
    if not value.strip() or len(value) > 31 or re.search(r"[\x00-\x1f\[\]:*?/\\]", value):
        raise ValueError("Invalid worksheet name")
    return value


def coordinates(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", value)
    if not match:
        raise ValueError("Expected an Excel cell such as A1")
    column = 0
    for character in match[1]:
        column = column * 26 + ord(character) - ord("A") + 1
    row = int(match[2])
    if row > 1048576 or column > 16384:
        raise ValueError("Cell is outside Excel dimensions")
    return row, column


def cell_address(value: str) -> str:
    coordinates(value)
    return value


def range_address(value: str) -> str:
    parts = value.split(":")
    if len(parts) not in (1, 2):
        raise ValueError("Expected a bounded Excel cell range")
    first, last = coordinates(parts[0]), coordinates(parts[-1])
    if first[0] > last[0] or first[1] > last[1]:
        raise ValueError("Range end precedes start")
    if (last[0] - first[0] + 1) * (last[1] - first[1] + 1) > 200000:
        raise ValueError("Operation exceeds 200000 cells")
    return value


SheetName = Annotated[str, AfterValidator(sheet_name)]
CellAddress = Annotated[str, AfterValidator(cell_address)]
RangeAddress = Annotated[str, AfterValidator(range_address)]
Color = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{6}$")]
CellValue = StrictStr | StrictInt | FiniteFloat | StrictBool | None


class WriteCells(StrictModel):
    kind: Literal["write_cells"]
    sheet: SheetName
    start: CellAddress
    values: tuple[tuple[CellValue, ...], ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def bounds(self):
        row, column = coordinates(self.start)
        width = max(len(values) for values in self.values)
        if width > 100 or row + len(self.values) - 1 > 1048576 or column + width - 1 > 16384:
            raise ValueError("Cell values exceed worksheet bounds")
        if any(
            isinstance(cell, str) and len(cell) > 32767 for values in self.values for cell in values
        ):
            raise ValueError("Cell text exceeds Excel's limit")
        return self


class AddSheet(StrictModel):
    kind: Literal["add_sheet"]
    name: SheetName


class SetFormula(StrictModel):
    kind: Literal["set_formula"]
    sheet: SheetName
    cell: CellAddress
    formula: str = Field(pattern=r"^=", min_length=2, max_length=4096)


class FormatRange(StrictModel):
    kind: Literal["format_range"]
    sheet: SheetName
    range: RangeAddress
    bold: bool | None = None
    italic: bool | None = None
    font_size: int | None = Field(default=None, ge=8, le=72)
    font_color: Color | None = None
    fill_color: Color | None = None
    number_format: str | None = Field(default=None, max_length=128)
    alignment: Literal["left", "center", "right"] | None = None


class AddChart(StrictModel):
    kind: Literal["add_chart"]
    sheet: SheetName
    chart_type: Literal["column", "bar", "line"]
    data_range: RangeAddress
    categories_range: RangeAddress | None = None
    position: CellAddress = "E2"
    title: str = Field(default="", max_length=120)
    titles_from_data: bool = True


SpreadsheetEdit = Annotated[
    WriteCells | AddSheet | SetFormula | FormatRange | AddChart,
    Field(discriminator="kind"),
]


class SpreadsheetPlan(StrictModel):
    sheet_name: SheetName = "Sheet1"
    operations: tuple[SpreadsheetEdit, ...] = Field(default=(), max_length=200)
