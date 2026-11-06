import json
from dataclasses import replace
from urllib.parse import quote

from backend.schemas.google import GoogleFileRef, GoogleWritePreview, SheetChange, SheetSnapshot
from backend.services.google.access import google_url
from backend.services.google.client import SHEETS_URL
from backend.services.google.errors import GoogleServiceError
from backend.services.google.mutations import GoogleMutations, fingerprint
from backend.services.google.ranges import column, parse_range


def snippet(value):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= 11000 else text[:10950] + "… [preview truncated]"


class GoogleSheets:
    def __init__(self, access, sessions):
        self.access, self.sessions = access, sessions
        self.journal = GoogleMutations(access, sessions)

    async def resolve_tab(self, auth, bounds, pinned=None):
        response = await self.access.credentials.http.request(
            "GET",
            SHEETS_URL + "/" + quote(auth.ref.file_id, safe=""),
            token=auth.token,
            params={"fields": "sheets(properties(sheetId,title,index,hidden))"},
        )
        tabs = [item.get("properties", {}) for item in response.get("sheets", [])]
        if not tabs or any(
            type(tab.get("sheetId")) is not int or not isinstance(tab.get("title"), str)
            for tab in tabs
        ):
            raise GoogleServiceError(502, "Invalid Google sheet tab metadata")
        title = bounds.tab[1:-1].replace("''", "'") if bounds.tab.startswith("'") else bounds.tab
        if pinned is not None:
            tab = next((tab for tab in tabs if tab["sheetId"] == pinned["sheet_id"]), None)
            if tab is None or tab["title"] != pinned["title"]:
                raise GoogleServiceError(409, "Google sheet tab changed; prepare a new approval")
        elif title:
            tab = next((tab for tab in tabs if tab["title"] == title), None)
        else:
            tab = next(
                (
                    tab
                    for tab in sorted(tabs, key=lambda item: item.get("index", 0))
                    if not tab.get("hidden", False)
                ),
                None,
            )
        if tab is None:
            raise GoogleServiceError(422, "Google sheet tab not found")
        return {"sheet_id": tab["sheetId"], "title": tab["title"]}

    @staticmethod
    def format_request(bounds, tab, fmt):
        style, fields = {}, []
        for key in ("bold", "italic"):
            if fmt.get(key) is not None:
                style.setdefault("textFormat", {})[key] = fmt[key]
                fields.append("userEnteredFormat.textFormat." + key)
        if fmt.get("background"):
            value = fmt["background"]
            style["backgroundColorStyle"] = {
                "rgbColor": {
                    key: int(value[1 + i * 2 : 3 + i * 2], 16) / 255
                    for i, key in enumerate(("red", "green", "blue"))
                }
            }
            fields.append("userEnteredFormat.backgroundColorStyle")
        if fmt.get("number_format"):
            style["numberFormat"] = {"type": fmt["number_format"]}
            fields.append("userEnteredFormat.numberFormat")
        if not fields:
            raise GoogleServiceError(422, "Choose a formatting change")
        return {
            "repeatCell": {
                "range": {
                    "sheetId": tab["sheet_id"],
                    "startRowIndex": bounds.start - 1,
                    "endRowIndex": bounds.end,
                    "startColumnIndex": column(bounds.first_column),
                    "endColumnIndex": column(bounds.last_column) + 1,
                },
                "cell": {"userEnteredFormat": style},
                "fields": ",".join(fields),
            }
        }

    async def formatting(self, auth, bounds, tab, fmt):
        response = await self.access.credentials.http.request(
            "GET",
            SHEETS_URL + "/" + quote(auth.ref.file_id, safe=""),
            token=auth.token,
            params={
                "ranges": bounds.a1(),
                "fields": "sheets(properties(sheetId,title),"
                "data(startRow,startColumn,rowData(values(userEnteredFormat))))",
            },
        )
        sheets = response.get("sheets", [])
        if (
            len(sheets) != 1
            or sheets[0].get("properties", {}).get("sheetId") != tab["sheet_id"]
            or sheets[0]["properties"].get("title") != tab["title"]
        ):
            raise GoogleServiceError(
                409, "Google formatting target changed; prepare a new approval"
            )
        cells = {}
        for grid in sheets[0].get("data", []):
            rows = grid.get("rowData", [])
            if len(rows) > 1000:
                raise GoogleServiceError(502, "Google formatting response exceeds bounds")
            for row_index, row in enumerate(rows, grid.get("startRow", 0)):
                values = row.get("values", [])
                if len(values) > 50:
                    raise GoogleServiceError(502, "Google formatting response exceeds bounds")
                for col_index, cell in enumerate(values, grid.get("startColumn", 0)):
                    if not (
                        bounds.start - 1 <= row_index < bounds.end
                        and column(bounds.first_column) <= col_index <= column(bounds.last_column)
                    ):
                        raise GoogleServiceError(502, "Google formatting response outside target")
                    source = cell.get("userEnteredFormat", {})
                    selected = {}
                    for key in ("bold", "italic"):
                        if fmt.get(key) is not None:
                            selected[key] = source.get("textFormat", {}).get(key, False)
                    if fmt.get("background"):
                        selected["background"] = {
                            key: source.get(key)
                            for key in ("backgroundColor", "backgroundColorStyle")
                        }
                    if fmt.get("number_format"):
                        selected["number_format"] = source.get("numberFormat")
                    cells[f"{row_index}:{col_index}"] = selected
        return cells

    async def values(self, auth, target, *, render="FORMULA"):
        response = await self.access.credentials.http.request(
            "GET",
            SHEETS_URL
            + "/"
            + quote(auth.ref.file_id, safe="")
            + "/values/"
            + quote(target, safe=""),
            token=auth.token,
            params={"valueRenderOption": render, "majorDimension": "ROWS"},
        )
        values = response.get("values", [])
        bounds = parse_range(target)
        if not isinstance(response.get("range"), str) or not response["range"]:
            raise GoogleServiceError(502, "Invalid Google ValueRange response")
        if (
            not isinstance(values, list)
            or len(values) > bounds.end - bounds.start + 1
            or any(not isinstance(row, list) or len(row) > bounds.columns for row in values)
            or len(json.dumps(values, ensure_ascii=False)) > 200000
        ):
            raise GoogleServiceError(
                422, "Google sheet observation exceeds bounds; use a smaller range"
            )
        return values

    async def read(self, context, ref, *, range_a1, offset=0, limit=100):
        bounds = parse_range(range_a1)
        if not 1 <= limit <= 100 or not 0 <= offset <= bounds.end - bounds.start:
            raise GoogleServiceError(422, "Invalid Google sheet page")
        target = bounds.a1(offset, limit)
        async with self.sessions() as session:
            auth = await self.access.authorize(session, context, ref, "read_sheet")
        values = await self.values(auth, target, render="UNFORMATTED_VALUE")
        next_offset = offset + limit if offset + limit <= bounds.end - bounds.start else None
        return SheetSnapshot(
            file_id=ref.file_id,
            range_a1=target,
            values=values,
            snapshot_hash=fingerprint(values),
            next_offset=next_offset,
            warnings=["Observation is paginated; continue with next_offset."]
            if next_offset is not None
            else [],
        )

    async def prepare(self, context, ref, change):
        change = SheetChange.model_validate(change)
        bounds = parse_range(change.range_a1)
        if change.rows and (
            len(change.rows[0]) > bounds.columns
            or (change.kind != "append_rows" and len(change.rows) > bounds.end - bounds.start + 1)
        ):
            raise GoogleServiceError(422, "Write matrix exceeds the selected range")
        async with self.sessions() as session:
            auth = await self.access.authorize(session, context, ref, "write_sheet")
        tab = await self.resolve_tab(auth, bounds)
        bounds = replace(bounds, tab="'" + tab["title"].replace("'", "''") + "'")
        before = (
            await self.formatting(auth, bounds, tab, change.format.model_dump())
            if change.kind == "format_range"
            else await self.values(auth, bounds.a1())
        )
        request = {"ref": ref.model_dump(mode="json"), "change": change.model_dump(mode="json")}
        data = change.model_dump(mode="json")
        data["range_a1"], data["tab"] = bounds.a1(), tab
        if change.kind == "format_range":
            data["compiled_request"] = self.format_request(bounds, tab, data["format"])
        if change.dedup_column is not None:
            index = change.dedup_column
            seen = {str(row[index]).strip() for row in before if len(row) > index}
            rows = []
            for row in change.rows:
                key = str(row[index]).strip()
                if key and key not in seen:
                    rows.append(row)
                    seen.add(key)
            data["rows"] = rows
        preview = GoogleWritePreview(
            operation=change.kind,
            title=f"{change.kind}: {auth.name}",
            target=google_url(ref.file_id, auth.mime_type) + " · " + bounds.a1(),
            before=snippet(before),
            after=snippet(data["format"] if change.kind == "format_range" else data["rows"]),
            warning=(
                "Preview may be truncated. RAW text stays text; formulas require an explicit "
                "operation. External edits can still race after the final snapshot check; "
                "no cross-service transaction is promised."
            ),
        )
        return await self.journal.prepare(
            context,
            connection_id=ref.connection_id,
            file_id=ref.file_id,
            connection_generation=auth.connection_generation,
            grant_generation=auth.grant_generation,
            kind=change.kind,
            request=request,
            data=data,
            snapshot_hash=fingerprint({"tab": tab, "before": before}),
            preview=preview,
        )

    async def execute(self, context, *, proposal_id, digest, preview):
        operation = (
            preview.operation
            if isinstance(preview, GoogleWritePreview)
            else preview.get("operation")
        )
        tool_name = "google_sheets_append" if operation == "append_rows" else "google_sheets_update"

        async def authorize(session, row):
            if row.kind not in {"append_rows", "update_range", "set_formulas", "format_range"}:
                raise GoogleServiceError(422, "Proposal is not a Sheets write")
            return await self.access.authorize(
                session,
                context,
                GoogleFileRef.model_validate(row.payload["request"]["ref"]),
                "write_sheet",
            )

        async def snapshot(row, auth):
            data = row.payload["data"]
            bounds = parse_range(data["range_a1"])
            tab = await self.resolve_tab(auth, bounds, data["tab"])
            if row.kind == "format_range":
                self.format_request(bounds, tab, data["format"])
                before = await self.formatting(auth, bounds, tab, data["format"])
            else:
                before = await self.values(auth, bounds.a1())
            return fingerprint({"tab": tab, "before": before})

        async def write(row, auth, session):
            change = row.payload["data"]
            bounds = parse_range(change["range_a1"])
            base = SHEETS_URL + "/" + quote(auth.ref.file_id, safe="")
            if row.kind == "format_range":
                response = await self.access.credentials.http.request(
                    "POST",
                    base + ":batchUpdate",
                    token=auth.token,
                    json={"requests": [change["compiled_request"]]},
                )
                if response.get("spreadsheetId") != auth.ref.file_id:
                    raise GoogleServiceError(502, "Invalid Google formatting receipt")
                result = {"updatedRange": bounds.a1(), "updatedRows": bounds.end - bounds.start + 1}
            elif not change["rows"]:
                result = {"updatedRange": "", "updatedRows": 0, "updatedCells": 0}
            else:
                append = row.kind == "append_rows"
                response = await self.access.credentials.http.request(
                    "POST" if append else "PUT",
                    base + "/values/" + quote(bounds.a1(), safe="") + (":append" if append else ""),
                    token=auth.token,
                    params={
                        "valueInputOption": "USER_ENTERED" if row.kind == "set_formulas" else "RAW",
                        **({"insertDataOption": "INSERT_ROWS"} if append else {}),
                    },
                    json={"majorDimension": "ROWS", "values": change["rows"]},
                )
                result = response.get("updates", {}) if append else response
                if (
                    not isinstance(result, dict)
                    or not isinstance(result.get("updatedRange"), str)
                    or not result["updatedRange"]
                    or any(
                        type(result.get(key, 0)) is not int or result.get(key, 0) < 0
                        for key in ("updatedRows", "updatedCells")
                    )
                ):
                    raise GoogleServiceError(502, "Invalid Google Sheets write receipt")
            return {
                "updated_range": str(result.get("updatedRange", ""))[:200],
                "updated_rows": max(0, int(result.get("updatedRows", 0))),
                "updated_cells": max(0, int(result.get("updatedCells", 0))),
                "url": google_url(auth.ref.file_id, auth.mime_type),
            }

        return await self.journal.execute(
            context,
            proposal_id=proposal_id,
            digest=digest,
            preview=preview,
            tool_name=tool_name,
            authorize=authorize,
            snapshot=snapshot,
            write=write,
        )
