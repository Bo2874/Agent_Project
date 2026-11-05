import hashlib
import io
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Cm, Pt
from pypdf import PdfReader

from artifact_runtime.containers import DockerBroker
from artifact_runtime.protocol import ArtifactRunRequest, BlobDescriptor, unpack_bundle


def qa_document(path):
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Cm(2)
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "DejaVu Sans", Pt(10)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1
    document.styles["Heading 1"].font.name = "DejaVu Sans"
    document.styles["Heading 1"].font.size = Pt(16)
    document.add_heading(
        "Báo cáo kiểm tra tài liệu tiếng Việt: tiêu đề dài xuống dòng, "
        "bảng nhiều trang và nội dung không bị cắt",
        1,
    )
    document.add_paragraph("Mẫu nghiệm thu Word/PDF. Các dấu: ă â đ ê ô ơ ư, Á Ấ Ặ Ề Ễ Ộ Ớ Ừ Ỹ.")
    table = document.add_table(rows=1, cols=2)
    table.style, table.autofit = "Table Grid", False
    table.columns[0].width, table.columns[1].width = Cm(1.6), Cm(15.4)
    table.cell(0, 0).text, table.cell(0, 1).text = "STT", "Nội dung và kết quả kiểm tra"
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for index in range(1, 76):
        row = table.add_row()
        row.cells[0].width, row.cells[1].width = Cm(1.6), Cm(15.4)
        row.cells[0].text = f"{index:02d}"
        row.cells[1].text = (
            f"Hạng mục {index:02d}: kiểm tra nội dung tiếng Việt, định dạng bảng và kết quả xử lý. "
            "Giữ đầy đủ thông tin khi chuyển trang, không mất dấu hoặc cắt chữ."
        )
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    document.add_paragraph("KẾT THÚC: Đã kiểm tra đủ 75 hạng mục và giữ toàn bộ nội dung.")
    document.save(path)
    return path


async def test_vietnamese_long_heading_and_three_page_table_render(tmp_path):
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires the real Writer/Poppler render image")
    source = qa_document(tmp_path / "fixture.docx")
    broker = DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker"))
    data = source.read_bytes()
    started = time.monotonic()
    bundle = await broker.run(
        ArtifactRunRequest(
            job_id=uuid4(),
            action="render_document",
            inputs=(BlobDescriptor.from_bytes("input-0.docx", data),),
        ),
        (io.BytesIO(data),),
    )
    with bundle.open("rb") as stream:
        writer, outputs = unpack_bundle(stream, tmp_path / "pdf")
    pdf = outputs[0]
    assert writer.preview_manifest["render_metrics"]["container_peak_bytes"] > 0
    extracted = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "Đã kiểm tra đủ 75 hạng mục" in extracted
    assert "Hạng mục 01" in extracted and "Hạng mục 75" in extracted
    assert len(PdfReader(pdf).pages) == 3
    content = pdf.read_bytes()
    bundle = await broker.run(
        ArtifactRunRequest(
            job_id=uuid4(),
            action="render_pages",
            preview_limit=3,
            inputs=(BlobDescriptor.from_bytes("input-0.pdf", content),),
        ),
        (io.BytesIO(content),),
    )
    with bundle.open("rb") as stream:
        rendered, pages = unpack_bundle(stream, tmp_path / "pages")
    assert len(pages) == rendered.preview_manifest["page_count"] == 3
    assert all(page.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for page in pages)
    if target := os.environ.get("ARTIFACT_DOCUMENT_QA_DIR"):
        directory = Path(target)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "vietnamese-three-pages.docx").write_bytes(data)
        (directory / "vietnamese-three-pages.pdf").write_bytes(content)
        for index, page in enumerate(pages, 1):
            (directory / f"vietnamese-page-{index}.png").write_bytes(page.read_bytes())
        report = {
            "page_count": len(pages),
            "rows": 75,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "pdf_sha256": hashlib.sha256(content).hexdigest(),
            "writer": writer.preview_manifest.get("render_metrics"),
            "poppler": rendered.preview_manifest.get("render_metrics"),
        }
        (directory / "renderer.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
