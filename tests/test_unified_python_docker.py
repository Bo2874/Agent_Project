"""Opt-in tests of the actual shared image, with no model/API credentials."""
# ruff: noqa: F811 -- shared opt-in broker fixture is intentionally imported.

from uuid import uuid4

from test_artifact_runner import real_broker  # noqa: F401

from artifact_runtime.protocol import ArtifactOperation, ArtifactRunRequest, unpack_bundle


async def run(broker, tmp_path, code, **arguments):
    request = ArtifactRunRequest(
        job_id=uuid4(),
        operation=ArtifactOperation(
            kind="python",
            code=code,
            python={"code": code, **arguments},
        ),
    )
    path = await broker.run(request, ())
    with path.open("rb") as stream:
        report, files = unpack_bundle(stream, tmp_path / str(request.job_id))
    return request, path, report, files


async def test_all_common_libraries_and_uno_import_in_isolated_image(real_broker, tmp_path):
    _, _, report, files = await run(
        real_broker,
        tmp_path,
        """
import numpy, pandas, scipy, sympy, statsmodels, sklearn
import openpyxl, xlsxwriter, docx, docxtpl, pypdf, pdfplumber, reportlab
import matplotlib, seaborn, PIL, regex, rapidfuzz, dateutil, yaml, lxml, bs4
import jinja2, requests, httpx, pydantic, defusedxml, fastapi, uvicorn, uno
import os, socket
from pathlib import Path
assert os.getuid() == 65532
assert not Path('/var/run/docker.sock').exists()
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
    raise AssertionError('network accessible')
except OSError:
    pass
assert not any('SECRET' in k or 'TOKEN' in k or 'KEY' in k for k in os.environ)
print(numpy.array([1,2,3]).sum())
print(input())
""",
        stdin="hello",
    )
    assert report.execution.status == "success", report.execution.stderr
    assert report.execution.stdout.splitlines() == ["6", "hello"]
    assert files == ()


async def test_zero_output_receipt_replays_after_broker_restart(real_broker, tmp_path):
    from artifact_runtime.containers import DockerBroker

    request, path, report, _ = await run(real_broker, tmp_path, "print('receipt without files')")
    before = path.stat().st_mtime_ns
    restarted = DockerBroker(real_broker.root, docker=real_broker.docker)
    assert await restarted.run(request, ()) == path
    assert path.stat().st_mtime_ns == before
    assert report.execution.stdout == "receipt without files\n"


async def test_advanced_excel_and_vietnamese_docx_pdf_png(real_broker, tmp_path):
    from openpyxl import load_workbook
    from pypdf import PdfReader

    _, _, report, files = await run(
        real_broker,
        tmp_path,
        """
from openpyxl import Workbook, load_workbook
from openpyxl.chart import PieChart, Reference
from docx import Document
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
import matplotlib.pyplot as plt
w = Workbook(); s = w.active
s.append(['Tháng', 'Doanh thu']); s.append(['Một', 10]); s.append(['Hai', 20])
s['B4'] = '=MEDIAN(B2:B3)'
c = PieChart(); c.add_data(Reference(s, min_col=2, min_row=1, max_row=3), titles_from_data=True)
s.add_chart(c, 'D1'); w.save('report.xlsx')
assert load_workbook('report.xlsx').active['B4'].value == '=MEDIAN(B2:B3)'
d = Document(); d.add_paragraph('Báo cáo tiếng Việt'); d.save('scratch.docx')
assert Document('scratch.docx').paragraphs[0].text == 'Báo cáo tiếng Việt'
pdfmetrics.registerFont(TTFont('VN', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
p = canvas.Canvas('report.pdf'); p.setFont('VN', 14)
p.drawString(50, 750, 'Báo cáo tiếng Việt'); p.save()
plt.plot([1,2], [3,4]); plt.title('Doanh thu tiếng Việt'); plt.savefig('chart.png')
print('Verified XLSX, DOCX, PDF and PNG')
""",
        outputs=[{"path": "report.xlsx"}, {"path": "report.pdf"}, {"path": "chart.png"}],
        primary_output="report.xlsx",
    )
    assert report.execution.status == "success", report.execution.stderr
    assert len(load_workbook(files[0]).active._charts) == 1
    assert "Báo cáo tiếng Việt" in PdfReader(files[1]).pages[0].extract_text()
    assert files[2].read_bytes().startswith(b"\x89PNG")


async def test_missing_library_reports_import_error_and_next_call_has_no_ram(real_broker, tmp_path):
    _, _, first, _ = await run(
        real_broker, tmp_path, "remember = 7; import missing_library_for_runtime_qa"
    )
    assert first.execution.status == "error"
    assert "ModuleNotFoundError" in first.execution.stderr
    _, _, second, _ = await run(real_broker, tmp_path, "print('remember' in globals())")
    assert second.execution.stdout == "False\n"


async def test_output_symlink_is_rejected_in_actual_container(real_broker, tmp_path):
    _, _, report, files = await run(
        real_broker,
        tmp_path,
        "from pathlib import Path; Path('leak.txt').symlink_to('/etc/passwd')",
        outputs=[{"path": "leak.txt"}],
    )
    assert report.execution.status == "error"
    assert "Symlink" in report.execution.stderr
    assert files == ()
