# Unified Python execution and working files

Status: proposed final inventory; implementation has not started. The user selected a general-purpose Python tool with independent executions and persistent files, rather than persistent Python variables. This document makes the replacement and deletion scope reviewable.

## Intent and agreed behavior

`run_python_code` is the general computation/execution tool, not an Excel-specific generator. The LLM writes Python for calculations, data analysis/transformation, text processing, chart generation, and reading/creating/editing supported files. No separate create/edit tool or per-format operation schema should be required for routine requests.

Every call uses a fresh isolated execution container. Variables, imports, open handles and process memory do not survive. Selected input file versions and published outputs do survive in private storage. The agent can pass output version IDs into a later call to inspect or revise them. Preserve the existing chat behavior: real text streaming, one final output card after the answer completes, closed preview stays closed, reload/replay does not duplicate cards.

The scope covers the whole existing file workflow (Excel, DOCX, text PDF), general Python computations without files, and legacy small text outputs (TXT, Markdown, CSV, JSON). File/image/OCR understanding is not added by this refactor. Generated chart images can be outputs; viewing a preview in the browser must not be described as the text model visually reviewing it.

## Approaches and selection

1. Expand the old `code_execution` sandbox: retains the old executor but must rebuild binary input/output transport, storage and version integration. Its existing 5-second/128-MiB/text-only contract does not fit document work.
2. Consolidate on the existing Artifact transport and publication: retain private storage, scoped versions, cancellation and operation recovery; put a general Python interface in front and remove competing public tool paths. This matches the user's chosen direction and reuses the tested file pipeline.

Use approach 2. This is a refactor of the execution interface and environment, not a replacement of the artifact database or existing stored files.

## Final inventory

### One public execution tool

Keep **`run_python_code`** as the only public Python/file execution tool. Evolve its schema to accept Python source, optional text stdin, explicitly selected input version IDs, and explicit output declarations. Output declarations identify generated file paths/names and optional parent version IDs; they are not Excel/Word operations. The host validates paths and ownership and chooses the actual storage keys.

Return bounded stdout/stderr, execution status, warnings, and published file references. A calculation that writes no files must succeed. Multiple generated files may be retained internally, while the reply attaches only its selected final output. Code can reopen and validate its output within one execution, or the agent can inspect a saved output in a later call. Do not return binary data as inline base64 in model observations.

An output declaration contains its relative output path, display name and optional parent version ID. The call identifies one declared output as `primary_output`; a single output is the default primary. A successful zero-output/review call preserves the run's previously selected final output rather than clearing it. Multi-output calls must choose their primary explicitly. The host verifies the selected reference and binds only that reference to the completed message. Completion, restored timelines and replay use this persisted selection, not the newest ArtifactVersion timestamp. The existing latest-version rule remains only as a legacy history fallback.

Each execution has a durable receipt independent of file versions, using the existing AgentOperation journal where its invariants fit. Its fingerprint covers code/stdin, input version IDs and hashes, declared outputs/parents, primary selection, acknowledgements and runtime contract version. A zero-output success stores and replays its bounded stdout/status without rerunning code. Published children use deterministic per-output operation keys derived from the execution identity and output declaration. A repeated operation reconciles runner receipts and already published outputs before continuing; an unknown execution is never silently rerun. Do not pretend one ArtifactVersion is the execution receipt for a multi-output call.

Remove these from the new-run catalog and its prompt/routing metadata:

| Public tool | Replacement |
| --- | --- |
| `artifact_create` | Python code through `run_python_code` |
| `artifact_edit` | Python code plus pinned input and parent version |
| `run_artifact_python` | Merged into `run_python_code` |
| `artifact_inspect` | Python reading selected/output files; internal preview inspection remains |
| `artifact_rebuild_pdf_text_only` | No separate public format tool; preserve its omission/consent behavior in host validation and approval metadata |

There is no `create_excel` public tool in the current registry; do not invent a deletion target with that name. Non-Python tools (web search, Google Workspace, email, RAG, plugins, automation controls) remain in their own domains.

### One runtime image

Build **`personal-agent-python-runtime:1`** with the supported Python interpreter, Python execution/inspection code, Office/PDF/chart libraries and LibreOffice/UNO/Poppler. Initial library set: openpyxl, XlsxWriter, python-docx, pypdf, ReportLab, matplotlib and pandas, with compatible versions locked before implementation verification. This is an implementer choice supporting the user's requested general workflow; not a promise that every Python package is available.

The same image has two trusted entry points:

- Execution: disposable non-root container, no network, no host directories, no Docker socket or credentials. Includes internal inspect/recalculate/render actions used by the host, not public model tools.
- Broker: the existing Runner service, using the same image in Compose and given the Docker socket only in this role. Install the Docker CLI and HTTP service dependencies in this shared image. The execution role never receives the socket or runner signing key.

On native Windows, the broker may continue running from the project venv on loopback 8081. It launches children from the unified image. This does not require a second Docker image or persistent Python session.

Retire after successful replacement verification and deployment compatibility checks:

| Old image | Replacement |
| --- | --- |
| `personal-agent-python-sandbox:3.11-v1` | Unified runtime |
| `personal-agent-artifact-code:1` | Unified runtime |
| `personal-agent-artifact-render:1` | Unified runtime |
| `personal-agent-artifact-runner:dev` (including configured equivalents) | Compose broker uses unified runtime |

App/worker, PostgreSQL/pgvector, Redis and unrelated service images are outside this deletion scope. Do not use Docker prune. Check exact image IDs, tags and existing container references before deleting the specifically retired images on the user's machine.

## Components retained behind the tool

Keep Artifact/ArtifactVersion metadata, immutable private storage, user ownership, selected-file UI, downloads, message bindings, version picker, previews, operation journals, cancellation and worker recovery. Keep the Runner's HTTP/binary transport, but make its execution behavior general-purpose rather than limited to one generated Office file. Keep Office/PDF inspection and render code internally where useful; remove the requirement to express changes as `write_cells`, `set_formula`, `format_range`, `add_chart`, or Word block-edit schemas.

Only declared and authorized input versions are staged read-only in the container. The backend supplies deterministic relative input paths, file names and IDs in trusted tool context. An arbitrary local path in model code never grants host-file access. Output publication is performed by the host, not by LLM-written SQL or storage API calls.

## File handling and review

Separate structural/security validation from feature-support checks. Continue rejecting malformed archives, path traversal, symlinks, oversized output, macros/executable embedded content and unsafe external/executable links. Do not reject a valid workbook solely because it contains a supported library-generated chart beyond column/bar/line, or a safe feature the old structured editor could not preserve.

Never rewrite an existing file just to generate a preview. Preview, formula recalculation and document rendering are host services. If a conversion cannot preserve unsupported features, retain the original generated bytes and report the limitation; do not silently replace the file with a lossy conversion. New safe formulas are not confined to five functions. State calculation status truthfully; formula text alone does not establish a fresh cached result.

Retain text-PDF coverage warnings and explicit consent before discarding unreadable/image pages. The generic tool must not remove this existing behavior merely by dropping a dedicated tool name. Record any acknowledgement against the exact source version/hash and omitted pages in approval metadata, independently of model prose. No OCR is added.

Preflight every staged PDF on the host-controlled path, even if the model declares no parent for the output. Conservatively require a source-version/hash/page acknowledgement when a call receives a partially readable PDF and declares document outputs. Read-only analysis can return coverage warnings without acknowledging a lossy rewrite. The host cannot infer preservation from arbitrary Python or from the model's description of its code.

A newly generated PDF may be independent (for example, ReportLab/pypdf output); it does not require a DOCX companion. Editable companion sources are optional, explicitly associated outputs. Preserve companions and downloads of existing PDF versions. Apply safe PDF validation/rendering to the declared PDF itself.

For self-review, Python can reopen outputs, check cell contents/formulas, parse documents and print bounded summaries. A rendered image may be displayed to the user. Visual inspection by the LLM requires an explicitly supported vision input path, which is outside this refactor; do not claim it happened.

Legacy text artifacts remain bounded and downloadable through the same storage system. PNG output uses authenticated image preview; TXT/MD/CSV/JSON use safe text preview or download. Preserve existing working-file selection for Office/PDF, and allow the generic tool to reuse its own supported outputs. Do not add a tool per extension.

## Prompt and discovery

The tool description explains general execution capabilities, installed packages, stateless calls, input/output conventions, stdout/error handling, and the ability to create/edit files. Include Excel/Word/PDF/chart examples without making the tool exclusively a file generator. Remove the old instruction to use Python only if a narrower tool cannot solve the task, and remove the preference for now-retired artifact tools.

Update all discovery layers: registration, allowlist defaults, routing/shortlisting, execution-context metadata, Agent prompts, automation authority, output publication and operation recovery. A correct prompt is insufficient if the tool is filtered out before the model sees it. Retain code-execution approval policy; this refactor does not silently authorize previously unapproved arbitrary code or broaden automation file grants.

Visible consequence for approval: structured create/edit actions previously permitted by an automation's artifact grants may now wait for Python-code approval. Do not automatically reinterpret an existing create/edit grant as permission to execute arbitrary generated Python. Preserve pause/resume and show this consequence in rollout documentation. Any future preauthorization of constrained automation code is a separate explicit policy decision, not an incidental effect of renaming tools.

## Limits and configuration

Use one execution resource profile suitable for general computations and documents, with resource limits independent of the user's total agent token budget. Start from the current larger artifact profile (1 CPU, 1,024 MiB RAM, 90-second wall time per execution, no network); verify actual child-process CPU/time limits against calculations, Office generation and rendering before claiming compatibility. Keep 20-MiB/file and 40-MiB aggregate transport limits initially, bounded logs, and private-storage quotas. A no-output call is valid; no artificial 8-KiB text-only ceiling remains.

Use existing `PYTHON_SANDBOX_ENABLED` to enable the unified tool. `WORKSPACE_ARTIFACTS_ENABLED` continues to control working-file UI/APIs; file publication requires that functionality enabled. Samples and Compose must expose the correct enablement; do not silently enable arbitrary code when a deployment explicitly disabled it. Native development currently has Python disabled and Artifacts enabled; document the one-time switch needed after rollout without copying or overwriting the real `.env`.

Keep runner URL/key/storage settings compatible unless an implementation constraint requires a documented rename. Do not create a new configuration flag for each file type. Do not edit user credentials or migrate database contents merely to rename tools/images.

## Compatibility and retirement

Existing files, hashes, version IDs, download URLs and message history must remain readable. Stored operation keys/checkpoints/approval records must not be silently rewritten to new semantics or cause code to run twice. Before deleting old execution code/images, detect and handle old nonterminal runs: drain them under the old version, or provide a narrowly scoped compatibility path preserving the old operation fingerprint and approved payload. Hidden compatibility is only for existing runs, not a second public execution tool for new work. Document which code can be removed immediately and which must wait for the compatibility gate.

Retire the old standalone `agent_core/code_execution` executor/contract and `infra/docker/python-sandbox` image definition once their general-computation behavior is covered by the unified implementation. Migrate active scripts/tests to the unified contract; retain historical acceptance documents as history. Consolidate `infra/docker/artifact-sandbox` and `infra/docker/artifact-runner` into one runtime image definition/lockfile; preserve useful internals rather than deleting version/storage services.

Docker image deletion is the last step after real-container create/edit/review/download and general-calculation checks pass. If old images are still referenced by active/unmigrated containers, report that concrete dependency rather than forcing removal.

## Acceptance

1. Registry exposes exactly one Python/file execution tool, `run_python_code`, including a configuration check against duplicate registration.
2. General computation and stdin work without generated files; stdout/errors are bounded and code errors permit an intentional corrected call.
3. Create and edit Excel with styles, merged cells, formulas and a non-legacy chart using Python, then reopen the saved output in another independent execution. Original version remains downloadable.
4. Create/edit DOCX and create/read text PDF; retain PDF coverage/consent behavior. Verify legacy text outputs and generated PNG output.
5. Selected uploads and earlier outputs can be staged; unselected/foreign versions and arbitrary host paths cannot.
6. Preview limitations do not corrupt or silently drop valid generated content. Calculation freshness is verified separately.
7. Code requiring unavailable imports gets a clear error; network/secret/socket/resource isolation remains enforced. RAM state does not persist.
8. One final file card, real streaming, cancellation, replay, approvals, recovery and scheduled-automation authority continue to work.
9. Real Docker execution and Compose startup use only the unified runtime image for this subsystem. The optional native broker remains supported.
10. No live `.env`, user files or unrelated Docker resources are modified by tests or cleanup. Final retirement inventory names the actual removed code, tools and image tags.

## Builder check

One independent builder check found three missing contracts: PDF acknowledgement must cover all staged inputs, PDFs must not require a DOCX companion, and generic calls need durable zero/multi-output receipts plus explicit final-output selection. These are resolved above. The reviewer found no remaining unavoidable user-only implementation question. The Python approval consequence is explicitly disclosed; this proposed document is ready for user review before product changes.
