"""Document edits target a pinned outline and may not silently discard content."""

import hashlib
import zipfile
from uuid import uuid4

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Pt
from pydantic import ValidationError

from artifact_runtime.protocol import ArtifactOperation, ArtifactRunRequest


def source_document(path):
    document = Document()
    document.add_heading("Báo cáo ban đầu", level=1)
    paragraph = document.add_paragraph("Giữ ")
    paragraph.add_run("định dạng").bold = True
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Hạng mục"
    table.cell(0, 1).text = "Số lượng"
    table.cell(1, 0).text = "Sách"
    table.cell(1, 1).text = "10"
    document.save(path)
    return path


def test_docx_edit_heading_table_and_keep_original(tmp_path):
    from artifact_runtime.document import apply_document, inspect_docx

    path = source_document(tmp_path / "source.docx")
    original = path.read_bytes()
    outline = inspect_docx(path)
    heading = next(node for node in outline.nodes if node.kind == "heading")
    table = next(node for node in outline.nodes if node.kind == "table")
    report = apply_document(
        ArtifactOperation(
            kind="edit_docx",
            parent_version_id=uuid4(),
            payload={
                "operations": [
                    {
                        "kind": "replace_text",
                        "node_id": heading.node_id,
                        "old": "ban đầu",
                        "new": "đã duyệt",
                    },
                    {
                        "kind": "format_block",
                        "node_id": heading.node_id,
                        "style": "Heading 2",
                        "font_size": 18,
                    },
                    {
                        "kind": "set_table_cell",
                        "node_id": table.node_id,
                        "row": 1,
                        "column": 1,
                        "text": "15",
                    },
                ]
            },
        ),
        input_path=path,
        output_dir=tmp_path / "edited",
    )
    result = Document(tmp_path / "edited" / report.files[0].name)
    assert result.paragraphs[0].text == "Báo cáo đã duyệt"
    assert result.paragraphs[0].style.name == "Heading 2"
    assert result.paragraphs[0].runs[0].font.size == Pt(18)
    assert result.paragraphs[1].runs[1].bold is True
    assert result.tables[0].cell(1, 1).text == "15"
    assert result.tables[0].cell(1, 0).text == "Sách"
    assert path.read_bytes() == original
    assert report.files[0].mime_type.endswith("wordprocessingml.document")


def test_stale_node_id_rejected(tmp_path):
    from artifact_runtime.document import apply_document, inspect_docx

    first = source_document(tmp_path / "first.docx")
    old = inspect_docx(first).nodes[0].node_id
    document = Document(first)
    document.paragraphs[0].text = "A different pinned version"
    second = tmp_path / "second.docx"
    document.save(second)
    with pytest.raises(ValueError, match="[Ss]tale|[Ii]nput version"):
        apply_document(
            ArtifactOperation(
                kind="edit_docx",
                parent_version_id=uuid4(),
                payload={
                    "operations": [
                        {"kind": "replace_text", "node_id": old, "old": "Báo", "new": "Report"}
                    ]
                },
            ),
            input_path=second,
            output_dir=tmp_path / "refused",
        )
    assert not list((tmp_path / "refused").glob("*.docx"))


@pytest.mark.parametrize(
    "tag", ["drawing", "footnoteReference", "ins", "fldSimple", "sdt", "pPrChange"]
)
def test_unsupported_content_loss_requires_approval(tmp_path, tag):
    from artifact_runtime.document import apply_document

    path = source_document(tmp_path / "complex.docx")
    document = Document(path)
    document.paragraphs[1]._p.append(OxmlElement(f"w:{tag}"))
    document.save(path)
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="[Uu]nsupported|not supported"):
        apply_document(
            ArtifactOperation(
                kind="edit_docx",
                parent_version_id=uuid4(),
                payload={
                    "operations": [
                        {
                            "kind": "append_blocks",
                            "blocks": [{"kind": "paragraph", "text": "Must not publish"}],
                        }
                    ]
                },
            ),
            input_path=path,
            output_dir=tmp_path / "refused",
        )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    assert not list((tmp_path / "refused").glob("*.docx"))


def test_create_docx_with_list_and_table_escapes_text(tmp_path):
    from artifact_runtime.document import apply_document, inspect_docx

    report = apply_document(
        ArtifactOperation(
            kind="create_docx",
            payload={
                "blocks": [
                    {"kind": "heading", "text": "Kế hoạch", "level": 1},
                    {"kind": "paragraph", "text": "<script>alert('x')</script>"},
                    {"kind": "list", "text": "Mục thứ nhất", "ordered": True},
                    {"kind": "table", "rows": [["Tên", "Nội dung"], ["A", "Tiếng Việt có dấu"]]},
                ]
            },
        ),
        input_path=None,
        output_dir=tmp_path,
    )
    path = tmp_path / report.files[0].name
    outline = inspect_docx(path)
    assert [node.kind for node in outline.nodes] == ["heading", "paragraph", "list", "table"]
    assert outline.nodes[1].text == "<script>alert('x')</script>"
    assert outline.nodes[-1].rows[1][1] == "Tiếng Việt có dấu"
    with zipfile.ZipFile(path) as archive:
        content = archive.read("word/document.xml")
    assert b"&lt;script&gt;" in content


def test_replacement_across_runs_keeps_unrelated_formatting(tmp_path):
    from artifact_runtime.document import apply_document, inspect_docx

    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Hello ")
    paragraph.add_run("old ").bold = True
    paragraph.add_run("name").italic = True
    paragraph.add_run(" tail").underline = True
    path = tmp_path / "runs.docx"
    document.save(path)
    node = inspect_docx(path).nodes[0]
    report = apply_document(
        ArtifactOperation(
            kind="edit_docx",
            parent_version_id=uuid4(),
            payload={
                "operations": [
                    {
                        "kind": "replace_text",
                        "node_id": node.node_id,
                        "old": "old name",
                        "new": "new name",
                    }
                ]
            },
        ),
        input_path=path,
        output_dir=tmp_path / "result",
    )
    changed = Document(tmp_path / "result" / report.files[0].name).paragraphs[0]
    assert changed.text == "Hello new name tail"
    assert next(run for run in changed.runs if run.text == "new name").bold is True
    assert changed.runs[-1].text == " tail" and changed.runs[-1].underline is True


@pytest.mark.parametrize(
    "tag,attributes",
    [
        ("sym", {"font": "Wingdings", "char": "F0FC"}),
        ("noBreakHyphen", {}),
        ("softHyphen", {}),
        ("separator", {}),
        ("continuationSeparator", {}),
        ("lastRenderedPageBreak", {}),
        ("br", {"type": "page"}),
        ("br", {"type": "column"}),
        ("br", {"clear": "all"}),
    ],
)
def test_special_run_content_refused_before_adjacent_text_edit(tmp_path, tag, attributes):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    from artifact_runtime.document import apply_document

    document = Document()
    run = document.add_paragraph().add_run("Approved ")
    special = OxmlElement(f"w:{tag}")
    for key, value in attributes.items():
        special.set(qn(f"w:{key}"), value)
    run._r.append(special)
    run.add_text(" status")
    path = tmp_path / "special.docx"
    document.save(path)
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    with pytest.raises(ValueError, match="Unsupported DOCX"):
        apply_document(
            ArtifactOperation(
                kind="edit_docx",
                parent_version_id=uuid4(),
                payload={
                    "operations": [
                        {
                            "kind": "replace_text",
                            "node_id": f"{digest}:0",
                            "old": "Approved",
                            "new": "Reviewed",
                        }
                    ]
                },
            ),
            input_path=path,
            output_dir=tmp_path / "refused",
        )
    assert path.read_bytes() == original
    assert not list((tmp_path / "refused").glob("*"))


def test_docx_payload_rejects_code_and_unknown_operations():
    with pytest.raises(ValidationError):
        ArtifactOperation(
            kind="create_docx", payload={"operations": [{"kind": "shell", "command": "anything"}]}
        )
    with pytest.raises(ValidationError):
        ArtifactOperation(kind="create_docx", code="pass", payload={"blocks": []})


@pytest.mark.parametrize("kind", ["create_docx", "rebuild_pdf"])
def test_structured_operation_requires_a_payload(kind):
    with pytest.raises(ValidationError):
        ArtifactOperation(kind=kind)


def test_old_xlsx_request_payload_serialization_is_unchanged():
    # Saved broker fingerprints and operation receipts depend on this exact wire shape.
    payload = {"operations": [{"kind": "add_sheet", "name": "Other"}]}
    operation = ArtifactOperation(kind="create_xlsx", payload=payload)
    request = ArtifactRunRequest(job_id=uuid4(), operation=operation)
    assert request.model_dump(mode="json")["operation"]["payload"] == payload


def test_large_table_outline_is_bounded_and_reports_omissions(tmp_path):
    from artifact_runtime.document import inspect_docx

    document = Document()
    table = document.add_table(rows=55, cols=30)
    for cell in table.rows[0].cells:
        cell.text = "Long content " * 200
    path = tmp_path / "large.docx"
    document.save(path)
    outline = inspect_docx(path)
    assert len(outline.model_dump_json()) < 15000
    assert outline.nodes and outline.warnings
