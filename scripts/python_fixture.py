"""Deterministic offline model: generate Python, read back, stream a final answer."""

import json
import re

from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage


class OfflinePythonModel:
    provider, model, max_tokens = "fixture", "python-offline", 200

    async def aclose(self):
        pass

    async def stream(self, messages, tools=None, **kwargs):
        query = next(
            message["content"] for message in reversed(messages) if message["role"] == "user"
        )
        format_match = re.search(r"\b(docx|pdf)\b", query, re.I)
        format = format_match.group(1).lower() if format_match else "xlsx"
        edit = re.search(r"edit\s+([0-9a-f-]{36})", query)
        latest = messages[-1]
        if latest.get("role") == "tool":
            observation = json.loads(latest["content"])
            if observation.get("is_error"):
                raise AssertionError("Offline Python tool failed: " + observation["content"])
            if latest.get("tool_call_id") == "python-fixture-generate":
                ref = observation["data"]["artifact_refs"][0]
                readers = {
                    "xlsx": (
                        "from openpyxl import load_workbook; w=load_workbook(p); "
                        "assert w.active['B4'].value == '=SUM(B2:B3)'; "
                        "print('Verified Excel formula and structure')"
                    ),
                    "docx": (
                        "from docx import Document; d=Document(p); "
                        "assert d.paragraphs; print(d.paragraphs[0].text)"
                    ),
                    "pdf": (
                        "from pypdf import PdfReader; r=PdfReader(p); "
                        "assert r.pages; print(r.pages[0].extract_text())"
                    ),
                }
                args = {
                    "code": "from pathlib import Path; "
                    + f"p=Path('/input/input-0.{format}'); "
                    + readers[format],
                    "input_version_ids": [ref["version_id"]],
                }
                call = "python-fixture-readback"
            else:
                for index, token in enumerate(("Saved ", "a verified ", "file version.")):
                    yield LLMChunk(
                        content=token,
                        finish_reason="stop" if index == 2 else None,
                        usage=TokenUsage(total_tokens=30) if index == 2 else None,
                    )
                return
        elif tools:
            args = self.generation(format, edit.group(1) if edit else None)
            call = "python-fixture-generate"
        else:
            yield LLMChunk(content="No tools available.", finish_reason="stop")
            return
        yield LLMChunk(
            tool_call_deltas=(
                LLMToolCallDelta(
                    index=0,
                    id=call,
                    name="run_python_code",
                    arguments=json.dumps(args, ensure_ascii=False),
                ),
            ),
            finish_reason="tool_calls",
            usage=TokenUsage(total_tokens=30),
        )

    @staticmethod
    def generation(format, parent):
        output = f"fixture-report.{format}"
        heading = "Báo cáo đã sửa" if parent else "Báo cáo mẫu"
        if format == "xlsx":
            code = """
import xlsxwriter
"""
            if parent:
                code += (
                    "from openpyxl import load_workbook\n"
                    "assert load_workbook('/input/input-0.xlsx').active['A1'].value\n"
                )
            value = 20 if parent else 10
            code += f"""
w = xlsxwriter.Workbook('{output}')
s = w.add_worksheet('Sheet1'); w.add_worksheet('Notes')
s.write_row(0, 0, ['Month', 'Revenue'])
s.write_row(1, 0, ['Jan', {value}]); s.write_row(2, 0, ['Feb', 30])
s.write_formula('B4', '=SUM(B2:B3)', None, {value + 30})
c = w.add_chart({{'type': 'column'}})
c.add_series({{'values': '=Sheet1!$B$2:$B$3', 'categories': '=Sheet1!$A$2:$A$3'}})
c.set_title({{'name': 'Revenue'}}); s.insert_chart('D1', c)
w.close()
"""
        elif format == "docx":
            code = "from docx import Document\n"
            code += (
                "d=Document('/input/input-0.docx'); d.paragraphs[0].text='Báo cáo đã sửa'\n"
                if parent
                else (
                    "d=Document(); d.add_heading('Báo cáo mẫu', level=1)\n"
                    "d.add_paragraph('Nội dung tiếng Việt từ worker.')\n"
                    "t=d.add_table(rows=2, cols=2); t.cell(0,0).text='Mục'; "
                    "t.cell(1,0).text='Sách'\n"
                )
            )
            code += f"d.save('{output}')\n"
        else:
            code = ""
            if parent:
                code += (
                    "from pypdf import PdfReader\nassert PdfReader('/input/input-0.pdf').pages\n"
                )
            code += f"""
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
pdfmetrics.registerFont(TTFont('VN','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
c=canvas.Canvas('{output}'); c.setFont('VN', 16)
c.drawString(50,750,'{heading}'); c.save()
"""
        args = {"code": code, "outputs": [{"path": output}]}
        if parent:
            args["input_version_ids"] = [parent]
            args["outputs"][0]["parent_version_id"] = parent
        return args
