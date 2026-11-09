import pytest

from backend.schemas.google import DOC_MIME, SHEET_MIME
from backend.services.google.requests import validate_requests


@pytest.mark.parametrize(
    "mime,payload",
    [
        (
            SHEET_MIME,
            {
                "updateCells": {
                    "start": {"sheetId": 0},
                    "rows": [{"values": [{"userEnteredValue": {"formulaValue": "=SUM(A1:A10)"}}]}],
                    "fields": "userEnteredValue",
                }
            },
        ),
        (
            SHEET_MIME,
            {"sortRange": {"range": {"sheetId": 0}, "sortSpecs": [{"dimensionIndex": 0}]}},
        ),
        (
            SHEET_MIME,
            {
                "addChart": {
                    "chart": {
                        "spec": {"title": "Revenue", "basicChart": {"chartType": "COLUMN"}},
                        "position": {"overlayPosition": {"anchorCell": {"sheetId": 0}}},
                    }
                }
            },
        ),
        (DOC_MIME, {"insertText": {"location": {"index": 1, "tabId": "t1"}, "text": "Report"}}),
        (
            DOC_MIME,
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": 1, "endIndex": 5},
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields": "namedStyleType",
                }
            },
        ),
        (DOC_MIME, {"insertTable": {"rows": 3, "columns": 2, "location": {"index": 1}}}),
    ],
)
def test_content_layout_requests_are_supported(mime, payload):
    assert validate_requests(mime, {"requests": [payload]}) == {"requests": [payload]}


@pytest.mark.parametrize(
    "body",
    [
        {"requests": [{"updateCells": {}}], "spreadsheetId": "other"},
        {"requests": [{"addDataSource": {"dataSource": {}}}]},
        {"requests": [{"updateCells": {"spreadsheetId": "other"}}]},
        {
            "requests": [
                {
                    "updateCells": {
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {
                                            "formulaValue": '=IMPORTRANGE("other", "A1")'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        },
        {
            "requests": [
                {
                    "repeatCell": {
                        "cell": {
                            "userEnteredValue": {
                                "formulaValue": '=IMPORTXML("https://evil.test", "//x")'
                            }
                        }
                    }
                }
            ]
        },
        {"requests": [{"updateCells": {}, "deleteSheet": {"sheetId": 0}}]},
        {"requests": []},
        {"requests": [{"updateCells": {"authorization": "Bearer credential"}}]},
    ],
)
def test_external_authority_and_invalid_requests_are_rejected(body):
    with pytest.raises(ValueError):
        validate_requests(SHEET_MIME, body)


def test_doc_revision_is_host_owned_and_no_remote_image_fetch():
    for body in [
        {
            "requests": [{"insertText": {"text": "ok"}}],
            "writeControl": {"requiredRevisionId": "forged"},
        },
        {"requests": [{"insertInlineImage": {"uri": "https://evil.test/image"}}]},
    ]:
        with pytest.raises(ValueError):
            validate_requests(DOC_MIME, body)

