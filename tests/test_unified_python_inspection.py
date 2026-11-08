from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook
from openpyxl.chart import PieChart, Reference


def test_inspect_advanced_workbook_preserves_exact_bytes(tmp_path):
    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "report.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([1, 2])
    sheet.append([3, 4])
    sheet["C1"] = "=MEDIAN(A1:B2)"
    chart = PieChart()
    chart.add_data(Reference(sheet, min_col=1, min_row=1, max_row=2))
    sheet.add_chart(chart, "E1")
    workbook.save(path)
    before = path.read_bytes()
    report = inspect_file(path)
    assert any(cell.get("formula") == "=MEDIAN(A1:B2)" for cell in report.preview_manifest["cells"])
    assert report.calculation.status == "not_calculated"
    assert path.read_bytes() == before


def test_inspection_rejects_macro_in_archive(tmp_path):
    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "macro.xlsx"
    with ZipFile(path, "w", ZIP_DEFLATED) as output:
        output.writestr("[Content_Types].xml", "<Types/>")
        output.writestr("xl/workbook.xml", "<workbook/>")
        output.writestr("xl/vbaProject.bin", b"macro")
    with pytest.raises(ValueError, match="active|Macro|macro"):
        inspect_file(path)


def test_text_preview_is_bounded_and_plain(tmp_path):
    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "data.csv"
    path.write_text("<script>never execute</script>\n" * 2000)
    report = inspect_file(path)
    assert report.preview_manifest["kind"] == "text"
    assert len(report.preview_manifest["text"]) <= 16000
    assert report.preview_manifest["truncated"]
