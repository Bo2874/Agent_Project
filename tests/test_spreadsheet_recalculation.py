import io
import os
import zipfile
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference

from artifact_runtime.containers import DockerBroker, RunnerError
from artifact_runtime.protocol import (
    ArtifactOperation,
    ArtifactRunRequest,
    BlobDescriptor,
    unpack_bundle,
)
from artifact_runtime.spreadsheet import apply_spreadsheet, inspect_spreadsheet
from tests.test_spreadsheet_artifacts import expense_operation


async def test_calc_recalculates_cached_value_and_preserves_chart(tmp_path):
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires locally built artifact code/render images and Docker")
    before = tmp_path / "before"
    report = apply_spreadsheet(expense_operation(), input_path=None, output_dir=before)
    data = (before / report.files[0].name).read_bytes()
    broker = DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker"))
    path = await broker.run(
        ArtifactRunRequest(
            job_id=uuid4(),
            action="recalculate",
            inputs=(BlobDescriptor.from_bytes("input-0.xlsx", data),),
        ),
        (io.BytesIO(data),),
    )
    with path.open("rb") as source:
        manifest, outputs = unpack_bundle(source, tmp_path / "after")
    assert manifest.calculation.status == "fresh"
    assert load_workbook(outputs[0], data_only=True)["Expenses"]["C6"].value == 535000
    workbook = load_workbook(outputs[0])
    assert workbook["Expenses"]["C6"].value == "=SUM(C2:C5)"
    assert len(workbook.sheetnames) == 2
    assert len(workbook["Expenses"]._charts) == 1


async def test_calc_supported_formulas_and_chart_types(tmp_path):
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires artifact render image")
    workbook = Workbook()
    sheet = workbook.active
    for row in [["Group", "Amount"], ["A", 10], ["B", 20], ["A", 30]]:
        sheet.append(row)
    for index, formula in enumerate(
        [
            '=SUMIF(A2:A4,"A",B2:B4)',
            "=AVERAGE(B2:B4)",
            "=COUNT(B2:B4)",
            '=IF(B2=10,"yes","no")',
            '=IF(B2=10,"","no")',
        ],
        1,
    ):
        sheet.cell(index, 4, formula)
    for kind, position in [("col", "F1"), ("bar", "F20"), ("line", "F40")]:
        chart = LineChart() if kind == "line" else BarChart()
        if kind != "line":
            chart.type = kind
        chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=4), titles_from_data=True)
        sheet.add_chart(chart, position)
    source = io.BytesIO()
    workbook.save(source)
    content = source.getvalue()
    broker = DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker"))
    output = await broker.run(
        ArtifactRunRequest(
            job_id=uuid4(),
            action="recalculate",
            inputs=(BlobDescriptor.from_bytes("input-0.xlsx", content),),
        ),
        (io.BytesIO(content),),
    )
    with output.open("rb") as stream:
        manifest, paths = unpack_bundle(stream, tmp_path / "result")
    assert manifest.calculation.status == "fresh"
    cached = load_workbook(paths[0], data_only=True).active
    assert [cached.cell(index, 4).value for index in range(1, 5)] == [40, 20, 3, "yes"]
    assert cached["D5"].value in (None, "")
    preview = inspect_spreadsheet(paths[0])
    assert (
        next(cell for cell in preview.preview_manifest["cells"] if cell["address"] == "D5")[
            "cached_value"
        ]
        == ""
    )
    assert len(load_workbook(paths[0]).active._charts) == 3


async def test_structured_create_edit_in_real_sandbox(tmp_path):
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires artifact code image")
    broker = DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker"))
    result = await broker.run(ArtifactRunRequest(job_id=uuid4(), operation=expense_operation()), ())
    with result.open("rb") as source:
        manifest, paths = unpack_bundle(source, tmp_path / "original")
    content = paths[0].read_bytes()
    edited = await broker.run(
        ArtifactRunRequest(
            job_id=uuid4(),
            operation=ArtifactOperation(
                kind="edit_xlsx",
                parent_version_id=uuid4(),
                payload={
                    "operations": [
                        {
                            "kind": "write_cells",
                            "sheet": "Summary",
                            "start": "A1",
                            "values": [["Sandbox edit"]],
                        },
                    ]
                },
            ),
            inputs=(BlobDescriptor.from_bytes("input-0.xlsx", content),),
        ),
        (io.BytesIO(content),),
    )
    with edited.open("rb") as source:
        report, outputs = unpack_bundle(source, tmp_path / "edited")
    workbook = load_workbook(outputs[0])
    assert workbook["Summary"]["A1"].value == "Sandbox edit"
    assert workbook["Expenses"]["C6"].value == "=SUM(C2:C5)"
    assert paths[0].read_bytes() == content
    assert report.calculation.status != "fresh"


async def test_python_cannot_bypass_unsafe_input_preflight(tmp_path):
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires artifact code image")
    buffer = io.BytesIO()
    workbook = Workbook()
    workbook.save(buffer)
    malicious = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as source,
        zipfile.ZipFile(malicious, "w") as target,
    ):
        for item in source.infolist():
            target.writestr(item.filename, source.read(item))
        target.writestr("xl/vbaProject.bin", b"macro content")
    data = malicious.getvalue()
    broker = DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker"))
    request = ArtifactRunRequest(
        job_id=uuid4(),
        operation=ArtifactOperation(
            kind="python",
            code="from pathlib import Path; Path('/output/bypass.xlsx').write_bytes(b'bypass')",
        ),
        inputs=(BlobDescriptor.from_bytes("input-0.xlsx", data),),
    )
    with pytest.raises(RunnerError, match="[Mm]acro"):
        await broker.run(request, (io.BytesIO(data),))
    assert await broker.resources(request.job_id) == []
