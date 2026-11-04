import hashlib
import zipfile
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from pydantic import ValidationError

from artifact_runtime.protocol import ArtifactOperation, canonical
from artifact_runtime.spreadsheet import apply_spreadsheet, inspect_spreadsheet


def expense_operation():
    return ArtifactOperation(
        kind="create_xlsx",
        payload={
            "sheet_name": "Expenses",
            "operations": [
                {
                    "kind": "write_cells",
                    "sheet": "Expenses",
                    "start": "A1",
                    "values": [
                        ["Date", "Item", "Amount"],
                        ["2026-11-01", "Food", 120000],
                        ["2026-11-02", "Travel", 65000],
                        ["2026-11-03", "Books", 150000],
                        ["2026-11-04", "Supplies", 200000],
                        ["", "Total", None],
                    ],
                },
                {
                    "kind": "set_formula",
                    "sheet": "Expenses",
                    "cell": "C6",
                    "formula": "=SUM(C2:C5)",
                },
                {
                    "kind": "format_range",
                    "sheet": "Expenses",
                    "range": "A1:C1",
                    "bold": True,
                    "fill_color": "EEE9DF",
                },
                {"kind": "add_sheet", "name": "Summary"},
                {
                    "kind": "add_chart",
                    "sheet": "Expenses",
                    "chart_type": "column",
                    "data_range": "C1:C5",
                    "categories_range": "B2:B5",
                    "position": "E2",
                    "title": "Expenses",
                },
            ],
        },
    )


def test_create_and_edit_preserve_formula_and_original(tmp_path):
    original, edited = tmp_path / "original", tmp_path / "edited"
    report = apply_spreadsheet(expense_operation(), input_path=None, output_dir=original)
    path = original / report.files[0].name
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = apply_spreadsheet(
        ArtifactOperation(
            kind="edit_xlsx",
            parent_version_id=uuid4(),
            payload={
                "operations": [
                    {
                        "kind": "write_cells",
                        "sheet": "Summary",
                        "start": "A1",
                        "values": [["Reviewed"]],
                    },
                ]
            },
        ),
        input_path=path,
        output_dir=edited,
    )
    workbook = load_workbook(edited / result.files[0].name)
    assert workbook["Expenses"]["C6"].value == "=SUM(C2:C5)"
    assert len(workbook.sheetnames) == 2
    assert len(workbook["Expenses"]._charts) == 1
    assert workbook["Summary"]["A1"].value == "Reviewed"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert workbook["Expenses"]["A1"].font.bold


def test_stale_cache_never_reported_fresh(tmp_path):
    report = apply_spreadsheet(expense_operation(), input_path=None, output_dir=tmp_path)
    preview = inspect_spreadsheet(tmp_path / report.files[0].name)
    assert preview.calculation.status != "fresh"
    cell = next(cell for cell in preview.preview_manifest["cells"] if cell["address"] == "C6")
    assert cell["formula"] == "=SUM(C2:C5)"
    assert cell["cached_value"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"kind": "shell", "command": "rm -rf /"},
        {"kind": "write_cells", "sheet": "Expenses", "start": "XFE1", "values": [[1]]},
    ],
)
def test_structured_operations_reject_unknown_actions_and_invalid_cells(tmp_path, extra):
    with pytest.raises((ValueError, ValidationError)):
        apply_spreadsheet(
            ArtifactOperation(
                kind="create_xlsx",
                payload={
                    "operations": [extra],
                },
            ),
            input_path=None,
            output_dir=tmp_path,
        )


def test_text_cells_do_not_become_formulas(tmp_path):
    report = apply_spreadsheet(
        ArtifactOperation(
            kind="create_xlsx",
            payload={
                "operations": [
                    {
                        "kind": "write_cells",
                        "sheet": "Sheet1",
                        "start": "A1",
                        "values": [['=HYPERLINK("https://evil.example","x")']],
                    },
                ]
            },
        ),
        input_path=None,
        output_dir=tmp_path,
    )
    cell = load_workbook(tmp_path / report.files[0].name)["Sheet1"]["A1"]
    assert cell.data_type == "s"


@pytest.mark.parametrize(
    "part,content",
    [
        ("xl/externalLinks/externalLink1.xml", "<externalLink/>"),
        ("xl/vbaProject.bin", "macro"),
        ("xl/media/image1.png", "image"),
        ("xl/drawings/unsupported.xml", "<wsDr><sp/></wsDr>"),
    ],
)
def test_external_link_or_loss_requires_approval(tmp_path, part, content):
    report = apply_spreadsheet(expense_operation(), input_path=None, output_dir=tmp_path / "source")
    path = tmp_path / "source" / report.files[0].name
    changed = tmp_path / "external.xlsx"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(changed, "w") as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item))
        target.writestr(part, content)
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="[Ee]xternal|[Mm]acro|supported"):
        apply_spreadsheet(
            ArtifactOperation(
                kind="edit_xlsx",
                parent_version_id=uuid4(),
                payload={"operations": [{"kind": "add_sheet", "name": "Extra"}]},
            ),
            input_path=changed,
            output_dir=output,
        )
    assert list(output.glob("*.xlsx")) == []


def test_preview_is_bounded_and_does_not_truncate_original_file(tmp_path):
    workbook = Workbook()
    for _ in range(100):
        workbook.active.append(["long text " * 200] * 12)
    path = tmp_path / "long.xlsx"
    workbook.save(path)
    report = inspect_spreadsheet(path)
    assert len(canonical(report.model_dump(mode="json"))) <= 1048576
    assert load_workbook(path).active["A1"].value == "long text " * 200


def test_unsupported_formula_is_preserved_and_never_fresh(tmp_path):
    workbook = Workbook()
    workbook.active["A1"] = "=MEDIAN(1,2,9)"
    path = tmp_path / "unsupported.xlsx"
    workbook.save(path)
    from artifact_runtime.recalculation import recalculate_xlsx

    report = recalculate_xlsx(path, output_dir=tmp_path / "output")
    assert report.status == "unsupported"
    assert (
        load_workbook(tmp_path / "output" / "output-0.xlsx").active["A1"].value == "=MEDIAN(1,2,9)"
    )


def test_preview_chart_samples_are_bounded(tmp_path):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference

    from artifact_runtime.spreadsheet import inspect_spreadsheet

    book = Workbook()
    sheet = book.active
    for row in range(1, 302):
        sheet.append([f"Row {row}", row])
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=301))
    chart.set_categories(Reference(sheet, min_col=1, min_row=1, max_row=301))
    sheet.add_chart(chart, "E1")
    source = tmp_path / "chart-preview.xlsx"
    book.save(source)
    preview = inspect_spreadsheet(source).preview_manifest
    assert preview["charts"][0]["series"][0]["values"] == list(range(1, 101))
    assert preview["charts"][0]["categories"][0] == "Row 1"
    assert preview["charts"][0]["truncated"] is True
