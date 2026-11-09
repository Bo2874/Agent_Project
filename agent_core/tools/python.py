"""One general Python capability; storage and execution belong to the host service."""

from agent_core.contracts import CapabilityDescriptor
from agent_core.tools import ToolExecutionPolicy, ToolRoutingPolicy, create_context_tool
from artifact_runtime.protocol import PythonCodeInput

DESCRIPTION = (
    "Run general-purpose Python for calculations, data analysis, statistics, transformations, "
    "charts, and creating/reading/editing Excel (xlsx), Word (docx), text PDF, TXT, Markdown, "
    "CSV, JSON or PNG files. numpy/pandas/scipy/sympy/statsmodels/sklearn, openpyxl/xlsxwriter, "
    "python-docx/docxtpl, pypdf/pdfplumber/reportlab, matplotlib/seaborn/Pillow and common "
    "parsing libraries are preinstalled. A fresh isolated container runs every call: variables "
    "do not persist, network and runtime pip install are unavailable. Optional stdin supplies "
    "text input. Supply input_version_ids from selected_artifacts or outputs of this chat; "
    "files appear read-only at /input/input-0.<extension>, input-1.<extension>, etc. in the "
    "given order. Read using Python to inspect actual content before editing; never invent "
    "file data. Write outputs under /output (the working directory), declare each relative "
    "path and optional display name. To edit, include that input's parent_version_id in the "
    "output declaration: the original is retained. Multiple outputs require primary_output "
    "equal to one declared path; only this file is shown as the final chat card. Outputs are "
    "optional: print observations to stdout, including when checking an earlier generated "
    "file; a readback-only call preserves the previously selected final file. Result contains "
    "bounded stdout/stderr, execution status, warnings and immutable version refs. Inspect "
    "errors, correct code and call again when needed. Reopen generated files and print checks "
    "for content/formulas/structure before finalizing; preview pixels are not model vision. "
    "PDF may be created directly without a DOCX companion. Scanned/image PDF needs text-only "
    "clarification; no OCR. For partial PDF outputs the user must first reply exactly "
    "'Đồng ý chỉ xử lý văn bản' in chat; then supply text_only_pdf_inputs with the returned "
    "source version/hash/omitted pages. This clarifies content loss, not Python execution. "
    "State formula calculation/preview limitations truthfully. "
    "Google work uses this same tool when the connector is enabled. Use exact refs from "
    "selected_google_files (or Google outputs created in this run), and connection_id from "
    "google_connections. Declare google_inputs: native gives credential-free Google API "
    "JSON including cell/style metadata or Docs tabs/UTF-16 indices; download gives the "
    "selected binary; export requires export_mime. Google inputs follow local inputs in "
    "the /input/input-N.<extension> sequence (three combined inputs maximum). Sheets native "
    "defaults to A1:AX1000 on the first tab; specify range_a1 to inspect another named tab "
    "or smaller region. Read first, then infer precise edits from the user's content/layout "
    "request. Existing native documents retain their file ID: write a JSON file containing "
    "only {requests:[...]} with official Sheets/Docs batchUpdate content/layout requests, "
    "declare that output publish=false, and google_actions kind=batch_update with its "
    "request_path and Google input_index. Do not supply target IDs, credentials or writeControl "
    "inside the JSON; host binds the selected file and Docs revision. Native changes are "
    "automatic under the granted scope, without another user approval. For a new Google "
    "document create XLSX/DOCX normally and declare kind=upload, output_path, connection_id "
    "and mode=google_native; keep_format stores the original binary. Upload without input_index "
    "creates a new file; with input_index it replaces only a downloaded binary of the same "
    "format, never a native document. Conversion may lose features. External data/formulas, "
    "images, sharing/ownership/file deletion and arbitrary HTTP are unavailable. Maximum "
    "three Google actions, 100 requests per batch and 2 MiB native/control JSON. After writes, "
    "use another read call to verify actual Google content. Return host google_results URLs. "
    "If google_conflict or google_outcome_unknown occurs, stop Google work and report any "
    "completed actions; never reread/rebase/retry to bypass it. Ask for a new user request "
    "after a conflict, or reconciliation after unknown outcome. "
    "Return the final version ref only after the streamed answer, never fabricate download URLs."
)


def register_python_tool(registry, service):
    async def execute(context, arguments):
        return await service.execute(context, PythonCodeInput.model_validate(arguments))

    registry.register(
        create_context_tool(
            name="run_python_code",
            description=DESCRIPTION,
            args_schema=PythonCodeInput,
            coroutine=execute,
        ),
        policy=ToolExecutionPolicy(timeout_seconds=300, requires_approval=False),
        routing=ToolRoutingPolicy(
            keywords=frozenset(
                {
                    "python",
                    "code",
                    "calculate",
                    "data analysis",
                    "excel",
                    "xlsx",
                    "spreadsheet",
                    "word",
                    "docx",
                    "pdf",
                    "file",
                    "csv",
                    "json",
                    "google",
                    "sheets",
                    "docs",
                    "drive",
                    "chart",
                    "biểu đồ",
                    "bảng tính",
                    "tài liệu",
                    "tính toán",
                    "phân tích dữ liệu",
                }
            )
        ),
        capability=CapabilityDescriptor(
            capability_id="run_python_code",
            kind="tool",
            name="run_python_code",
            description=DESCRIPTION[:1000],
            version="4.0.0",
            side_effect="write",
            requires_approval=False,
            immutable_output=True,
        ),
    )
