"""Structure-aware LlamaIndex splitting with the embedding model's real tokenizer."""

import hashlib
import re
import unicodedata
from importlib.metadata import version

from agent_core.rag.documents import (
    ParsedBlock,
    ParsedDocument,
    PreparationError,
    PreparedChunk,
    PreparedDocument,
)

EMBEDDING_MODEL = "codefuse-ai/F2LLM-v2-0.6B"


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "".join(char for char in text if unicodedata.category(char) != "Cc" or char in "\n\t")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[^\S\n]+", " ", text)).strip()


def prepare_chunks(parsed: ParsedDocument, title: str, options: dict) -> PreparedDocument:
    from huggingface_hub import HfApi
    from llama_index.core.node_parser import SentenceSplitter
    from transformers import AutoTokenizer

    try:
        # Resolve main once and load by commit, so a revision change cannot mix tokenizers.
        revision = options["embedding_revision"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            revision = HfApi().model_info(EMBEDDING_MODEL, revision=revision).sha
        if not revision or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Missing tokenizer revision")
        tokenizer = AutoTokenizer.from_pretrained(
            EMBEDDING_MODEL,
            revision=revision,
            use_fast=True,
            trust_remote_code=False,
        )
        if not tokenizer.is_fast:
            raise ValueError("A fast tokenizer with offsets is required")
    except Exception as exc:
        raise PreparationError(
            "tokenizer_unavailable",
            "Chưa tải được tokenizer F2LLM. Kiểm tra kết nối/cache model.",
            retryable=True,
        ) from exc

    def tokens(text):
        return tokenizer.encode(normalize_text(text), add_special_tokens=False)

    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=True))

    def prefix_for(block, header=""):
        context = normalize_text("\n".join([title, *block.heading_path, header]))
        encoding = tokenizer(context, add_special_tokens=False, return_offsets_mapping=True)
        if len(encoding["input_ids"]) > 128:
            context = context[: encoding["offset_mapping"][127][1]]
            warnings.append(
                {
                    "code": "chunk_context_truncated",
                    "source_ref": block.source_ref,
                    "message": "Tiêu đề/header dài được rút gọn trong chunk; bản trích vẫn đầy đủ.",
                }
            )
        return context + "\n\n" if context else ""

    chunks, warnings = [], []

    def append(start, end, blocks, prefix, metadata=None):
        text = parsed.original_text[start:end]
        embedding_text = prefix + normalize_text(text)
        token_count = count(embedding_text)
        if token_count > 512 or not normalize_text(text):
            raise PreparationError(
                "chunk_token_limit", "Không chia được nội dung theo giới hạn token."
            )
        relevant = [block for block in blocks if block.start < end and block.end > start]
        starts = [block.page_start for block in relevant if block.page_start is not None]
        ends = [block.page_end for block in relevant if block.page_end is not None]
        chunks.append(
            PreparedChunk(
                text=text,
                embedding_text=embedding_text,
                lexical_text=embedding_text,
                token_count=token_count,
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                page_start=min(starts) if starts else None,
                page_end=max(ends) if ends else None,
                section=blocks[0].section,
                start_offset=start,
                end_offset=end,
                metadata_json={
                    "offset_basis": "parsed.original_text",
                    "heading_path": blocks[0].heading_path,
                    "source_refs": list(
                        dict.fromkeys(block.source_ref for block in relevant if block.source_ref)
                    ),
                    "trust": "untrusted",
                    **(metadata or {}),
                },
            )
        )
        if len(chunks) > options["max_chunks"]:
            raise PreparationError("document_chunk_limit", "Tài liệu có quá nhiều chunk.")

    def split_range(start, end, blocks, prefix, overlap=64, metadata=None):
        raw = parsed.original_text[start:end]
        if not normalize_text(raw):
            return
        if len(raw) > 50_000:
            for offset in range(start, end, 50_000):
                split_range(offset, min(offset + 50_000, end), blocks, prefix, overlap, metadata)
            return
        if count(prefix + normalize_text(raw)) <= 512:
            append(start, end, blocks, prefix, metadata)
            return
        # Base spans never overlap, so each span can be mapped exactly to the source.
        # Reserve room for a suffix of up to 64 tokens from the previous base span.
        size = 512 - count(prefix) - overlap - 12
        splitter = SentenceSplitter(
            chunk_size=size,
            chunk_overlap=0,
            tokenizer=tokens,
            chunking_tokenizer_fn=lambda value: re.split(r"(?<=[.!?。！？])(?=\s+\S)", value),
            secondary_chunking_regex=None,
            include_metadata=False,
            include_prev_next_rel=False,
        )
        previous_start, cursor = 0, 0
        for piece in splitter.split_text(raw):
            position = raw.find(piece, cursor)
            if position < 0 or raw[cursor:position].strip():
                raise PreparationError(
                    "chunk_source_mapping", "Không xác định được vị trí nguồn chunk."
                )
            new_end = position + len(piece)
            chunk_start = position
            if overlap and cursor:
                previous = raw[previous_start:cursor]
                offsets = tokenizer(
                    previous, add_special_tokens=False, return_offsets_mapping=True
                )["offset_mapping"]
                if offsets:
                    chunk_start = previous_start + offsets[max(0, len(offsets) - overlap)][0]
                    while (
                        chunk_start < position
                        and count(prefix + normalize_text(raw[chunk_start:new_end])) > 512
                    ):
                        chunk_start += 1
            append(start + chunk_start, start + new_end, blocks, prefix, metadata)
            previous_start, cursor = position, new_end
        if raw[cursor:].strip():
            raise PreparationError("chunk_source_mapping", "Chia chunk chưa bao phủ đủ nội dung.")

    def split_table(block):
        raw = parsed.original_text[block.start : block.end]
        rows = raw.splitlines(keepends=True)
        header_count = min(block.table_header_rows, len(rows))
        header = "".join(rows[:header_count]).strip()
        prefix = prefix_for(block, header)
        # Include header once as source evidence; repeat it in context for later row groups.
        start, row_start = block.start, 0
        end = start
        for row_index, row in enumerate(rows):
            if (
                end > start
                and count(prefix + normalize_text(parsed.original_text[start:end] + row)) > 512
            ):
                split_range(
                    start,
                    end,
                    [block],
                    prefix,
                    overlap=0,
                    metadata={
                        "table_header": header,
                        "row_start": row_start + 1,
                        "row_end": row_index,
                    },
                )
                start, row_start = end, row_index
            end += len(row)
            if count(prefix + normalize_text(parsed.original_text[start:end])) > 512:
                split_range(
                    start,
                    end,
                    [block],
                    prefix,
                    overlap=0,
                    metadata={
                        "table_header": header,
                        "row_start": row_start + 1,
                        "row_end": row_index + 1,
                        "row_fragment": True,
                    },
                )
                warnings.append(
                    {
                        "code": "table_row_split",
                        "source_ref": block.source_ref,
                        "row": row_index + 1,
                    }
                )
                start, row_start = end, row_index + 1
        if end > start and parsed.original_text[start:end].strip():
            split_range(
                start,
                end,
                [block],
                prefix,
                overlap=0,
                metadata={
                    "table_header": header,
                    "row_start": row_start + 1,
                    "row_end": len(rows),
                },
            )

    pending: list[ParsedBlock] = []

    def flush():
        if pending:
            split_range(pending[0].start, pending[-1].end, pending, prefix_for(pending[0]))
            pending.clear()

    for block in parsed.blocks:
        if block.kind in {"furniture", "table"}:
            flush()
            if block.kind == "table":
                split_table(block)
        else:
            if pending and block.heading_path != pending[0].heading_path:
                flush()
            pending.append(block)
    flush()
    if not chunks:
        raise PreparationError("document_no_chunks", "Không tạo được chunk văn bản để hỏi đáp.")
    return PreparedDocument(
        chunks=chunks,
        tokenizer_revision=revision,
        warnings=warnings,
        chunker_version=f"structure-v1/llama-index-{version('llama-index-core')}",
    )
