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


def test_defined_name_cannot_hide_external_function(tmp_path):
    from openpyxl.workbook.defined_name import DefinedName

    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "names.xlsx"
    workbook = Workbook()
    workbook.defined_names.add(DefinedName("Hidden", attr_text='WEBSERVICE("https://example.com")'))
    workbook.active["A1"] = "=Hidden"
    workbook.save(path)
    with pytest.raises(ValueError, match="executable|external"):
        inspect_file(path)


@pytest.mark.parametrize(
    "formula",
    [
        '=TEXTJOIN("|",TRUE,A1:A2)',
        '=IF(A1="WEBSERVICE(",1,0)',
        "=SUM(A1#)",
    ],
)
def test_text_literals_are_not_executable_formula_tokens(tmp_path, formula):
    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "literal.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "a"
    workbook.active["B1"] = formula
    workbook.save(path)
    before = path.read_bytes()
    inspect_file(path)
    assert path.read_bytes() == before


def test_pdf_passive_web_link_is_preserved(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        TextStringObject,
    )

    from artifact_runtime.file_inspection import inspect_file

    path = tmp_path / "linked.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (Example website) Tj ET")
    page[NameObject("/Contents")] = stream
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            ),
        }
    )
    page[NameObject("/Annots")] = ArrayObject(
        [
            DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject([FloatObject(n) for n in (10, 90, 150, 110)]),
                    NameObject("/A"): DictionaryObject(
                        {
                            NameObject("/S"): NameObject("/URI"),
                            NameObject("/URI"): TextStringObject("https://example.com"),
                        }
                    ),
                }
            )
        ]
    )
    with path.open("wb") as output:
        writer.write(output)
    before = path.read_bytes()
    inspect_file(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "action,target",
    [("/URI", "file:///evil.exe"), ("/JavaScript", "alert(1)"), ("/Launch", "/bin/sh")],
)
def test_pdf_executable_annotation_is_rejected(tmp_path, action, target):
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

    from artifact_runtime.pdf import read_pdf_text

    path = tmp_path / "active.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    page[NameObject("/Annots")] = ArrayObject(
        [
            DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/A"): DictionaryObject(
                        {
                            NameObject("/S"): NameObject(action),
                            NameObject("/URI" if action == "/URI" else "/JS"): TextStringObject(
                                target
                            ),
                        }
                    ),
                }
            )
        ]
    )
    with path.open("wb") as output:
        writer.write(output)
    with pytest.raises(ValueError, match="active|annotation"):
        read_pdf_text(path)
