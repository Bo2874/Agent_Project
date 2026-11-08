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


def test_large_unicode_text_preview_handles_partial_utf8_character(tmp_path):
    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "vietnamese.txt"
    path.write_bytes(("ệ" * 30000).encode())
    report = inspect_file(path)
    assert report.preview_manifest["text"] == "ệ" * 16000
    assert report.preview_manifest["truncated"]


def test_word_active_field_is_rejected_before_rendering(tmp_path):
    from docx import Document
    from docx.oxml import OxmlElement

    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "active.docx"
    document = Document()
    run = document.add_paragraph().add_run()
    field = OxmlElement("w:instrText")
    field.text = 'DDEAUTO "cmd" "/c calc.exe"'
    run._r.append(field)
    document.save(path)
    with pytest.raises(ValueError, match="field|active"):
        inspect_file(path)


def test_executable_file_hyperlink_formula_is_rejected(tmp_path):
    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "active.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = '=HYPERLINK("file:///C:/evil.exe","Click")'
    workbook.save(path)
    with pytest.raises(ValueError, match="executable|external|hyperlink"):
        inspect_file(path)
