"""Content/layout Google batch bodies; target, credentials and revision are host-owned."""

import json

from artifact_runtime.google_formulas import validate_formula
from backend.schemas.google import DOC_MIME, SHEET_MIME

SHEET_REQUESTS = frozenset(
    """
updateSpreadsheetProperties updateSheetProperties updateDimensionProperties
updateNamedRange repeatCell addNamedRange deleteNamedRange addSheet deleteSheet
autoFill cutPaste copyPaste mergeCells unmergeCells updateBorders updateCells
addFilterView appendCells clearBasicFilter deleteDimension deleteEmbeddedObject
deleteFilterView duplicateFilterView duplicateSheet insertDimension
insertRange moveDimension updateEmbeddedObjectPosition
updateFilterView setBasicFilter addConditionalFormatRule updateConditionalFormatRule
deleteConditionalFormatRule sortRange setDataValidation addChart updateChartSpec
autoResizeDimensions addBanding updateBanding deleteBanding createDeveloperMetadata
updateDeveloperMetadata deleteDeveloperMetadata randomizeRange deleteRange
appendDimension addDimensionGroup deleteDimensionGroup updateDimensionGroup trimWhitespace
deleteDuplicates updateEmbeddedObjectBorder
""".split()
)
DOC_REQUESTS = frozenset(
    """
insertText deleteContentRange replaceAllText updateTextStyle updateParagraphStyle
createParagraphBullets deleteParagraphBullets insertTable insertTableRow
insertTableColumn deleteTableRow deleteTableColumn updateTableCellStyle
updateTableColumnProperties updateTableRowStyle mergeTableCells unmergeTableCells
updateTableStyle pinTableHeaderRows insertPageBreak insertSectionBreak
updateDocumentStyle updateSectionStyle createHeader createFooter deleteHeader deleteFooter
createNamedRange deleteNamedRange updateDocumentTabProperties addDocumentTab deleteTab
""".split()
)
FORBIDDEN_KEYS = frozenset(
    {
        "spreadsheetId",
        "documentId",
        "fileId",
        "authorization",
        "access_token",
        "refresh_token",
        "token",
        "headers",
        "endpoint",
        "uri",
        "sourceUri",
        "dataSourceId",
        "dataSource",
        "dataSourceSpec",
        "linkedContentReference",
        "linkedChartId",
        "image",
        "imageProperties",
        "contentUri",
    }
)


def validate_requests(mime, body):
    allowed = SHEET_REQUESTS if mime == SHEET_MIME else DOC_REQUESTS if mime == DOC_MIME else ()
    if not allowed or not isinstance(body, dict) or set(body) != {"requests"}:
        raise ValueError(
            "Google control JSON must contain only requests; host owns target/revision"
        )
    requests = body["requests"]
    if not isinstance(requests, list) or not 1 <= len(requests) <= 100:
        raise ValueError("Google batch must contain 1 to 100 requests")
    # JSON serialization also rejects NaN and non-JSON values before any remote write.
    if len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode()) > 2097152:
        raise ValueError("Google request exceeds 2 MiB")

    def walk(value, depth=0):
        if depth > 40:
            raise ValueError("Google request nesting exceeds limit")
        if isinstance(value, dict):
            if FORBIDDEN_KEYS.intersection(value):
                raise ValueError("Google request includes an external target or credential field")
            for key, item in value.items():
                if not isinstance(key, str) or "_" in key:
                    raise ValueError("Use canonical Google JSON field names, not alternate aliases")
                if key == "formulaValue" or (key == "userEnteredValue" and isinstance(item, str)):
                    validate_formula(item)
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    for request in requests:
        if not isinstance(request, dict) or len(request) != 1 or next(iter(request)) not in allowed:
            raise ValueError("Unsupported Google content/layout request")
        if not isinstance(next(iter(request.values())), dict):
            raise ValueError("Google request payload must be an object")
        walk(request)
    return body
