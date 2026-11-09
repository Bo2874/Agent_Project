from uuid import uuid4

import pytest
from pydantic import ValidationError

from artifact_runtime.protocol import PythonCodeInput


def source(**kwargs):
    return {
        "ref": {"connection_id": uuid4(), "file_id": "selected", "context_id": uuid4()},
        **kwargs,
    }


def test_native_requests_are_control_outputs_not_final_artifacts():
    request = PythonCodeInput(
        code="print('read snapshot, build request')",
        google_inputs=[source(mode="native", range_a1="'Sales'!A1:D20")],
        outputs=[{"path": "changes.json", "publish": False}, {"path": "report.xlsx"}],
        google_actions=[{"kind": "batch_update", "input_index": 0, "request_path": "changes.json"}],
    )
    assert request.outputs[0].publish is False
    assert request.primary_output is None
    assert request.google_inputs[0].ref.file_id == "selected"


def test_upload_links_an_explicit_file_and_conversion_mode():
    connection = uuid4()
    request = PythonCodeInput(
        code="print('create report')",
        outputs=[{"path": "report.docx"}],
        google_actions=[
            {
                "kind": "upload",
                "connection_id": connection,
                "output_path": "report.docx",
                "mode": "google_native",
            }
        ],
    )
    assert request.google_actions[0].connection_id == connection
    assert request.google_actions[0].input_index is None


@pytest.mark.parametrize(
    "changes",
    [
        {"outputs": [{"path": "unclaimed.json", "publish": False}]},
        {"outputs": [{"path": "private.xlsx", "publish": False}]},
        {"google_actions": [{"kind": "batch_update", "input_index": 0, "request_path": "x.json"}]},
        {
            "google_inputs": [source(mode="native")],
            "outputs": [{"path": "x.json", "publish": False}],
            "google_actions": [
                {"kind": "batch_update", "input_index": 1, "request_path": "x.json"}
            ],
        },
        {"google_inputs": [source(mode="export")]},
        {"google_inputs": [source(mode="download", range_a1="A1:B2")]},
        {"google_inputs": [source(mode="native") for _ in range(4)]},
        {
            "input_version_ids": [uuid4(), uuid4(), uuid4()],
            "google_inputs": [source(mode="native")],
        },
        {
            "outputs": [{"path": "data.txt"}],
            "google_actions": [
                {
                    "kind": "upload",
                    "connection_id": uuid4(),
                    "output_path": "missing.txt",
                    "mode": "keep_format",
                }
            ],
        },
    ],
)
def test_invalid_google_control_contract_is_rejected(changes):
    with pytest.raises(ValidationError):
        PythonCodeInput(code="print(1)", **changes)


def test_original_python_wire_is_unchanged():
    request = PythonCodeInput(code="print(1)", outputs=[{"path": "a.txt"}])
    assert request.model_dump(mode="json") == {
        "code": "print(1)",
        "stdin": None,
        "input_version_ids": [],
        "outputs": [{"path": "a.txt", "name": None, "parent_version_id": None}],
        "primary_output": None,
    }


def test_only_control_output_needs_no_primary():
    request = PythonCodeInput(
        code="pass",
        google_inputs=[source(mode="native")],
        outputs=[{"path": "changes.json", "publish": False}],
        google_actions=[{"kind": "batch_update", "input_index": 0, "request_path": "changes.json"}],
    )
    assert request.primary_output is None


def test_same_snapshot_cannot_be_written_twice():
    with pytest.raises(ValidationError):
        PythonCodeInput(
            code="pass",
            google_inputs=[source(mode="native")],
            outputs=[{"path": "changes.json", "publish": False}],
            google_actions=[
                {"kind": "batch_update", "input_index": 0, "request_path": "changes.json"}
            ]
            * 2,
        )
