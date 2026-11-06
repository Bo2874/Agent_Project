import re
from dataclasses import dataclass

from backend.services.google.errors import GoogleServiceError

PATTERN = re.compile(
    r"(?:(?P<tab>'(?:[^']|'')+'|[^'!]+)!)?(?P<c1>[A-Za-z]{1,3})(?P<r1>[1-9]\d{0,6})(?::(?P<c2>[A-Za-z]{1,3})(?P<r2>[1-9]\d{0,6}))?"
)


def column(value):
    result = 0
    for letter in value.upper():
        result = result * 26 + ord(letter) - 64
    return result - 1


@dataclass(frozen=True)
class SheetRange:
    tab: str
    first_column: str
    last_column: str
    start: int
    end: int

    @property
    def columns(self):
        return column(self.last_column) - column(self.first_column) + 1

    def a1(self, offset=0, limit=None):
        stop = self.end if limit is None else min(self.end, self.start + offset + limit - 1)
        return (
            self.tab + "!" if self.tab else ""
        ) + f"{self.first_column}{self.start + offset}:{self.last_column}{stop}"


def parse_range(value):
    match = PATTERN.fullmatch(value)
    if not match:
        raise GoogleServiceError(422, "Use a bounded A1 range such as 'Sheet1!A1:D100'")
    groups = match.groupdict()
    result = SheetRange(
        groups["tab"] or "",
        groups["c1"].upper(),
        (groups["c2"] or groups["c1"]).upper(),
        int(groups["r1"]),
        int(groups["r2"] or groups["r1"]),
    )
    if (
        not 1 <= result.end - result.start + 1 <= 1000
        or not 1 <= result.columns <= 50
        or len(result.tab) > 100
        or any(ord(ch) < 32 for ch in value)
    ):
        raise GoogleServiceError(422, "Sheet range exceeds 1000 rows/50 columns or is reversed")
    return result
