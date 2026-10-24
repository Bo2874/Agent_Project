"""Lazy, serialized IPC to a reusable local embedding model; no model imports in the web app."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from agent_core.rag.local_models import MAX_FRAME_BYTES as MAX_FRAME_BYTES
from agent_core.rag.local_models import LocalModelClient, LocalModelError

MODEL = "codefuse-ai/F2LLM-v2-0.6B"
DIMENSIONS = 1024
RECIPE = "f2llm-st-query-prompt-document-eos-cosine-v1"
QUERY_PROMPT = (
    "Instruct: Given a question, retrieve passages that can help answer the question.\nQuery: "
)


class EmbeddingError(LocalModelError):
    pass


def validate_vectors(vectors: list, expected: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected:
        raise EmbeddingError("embedding_shape", "Số vector không khớp số đoạn văn bản.")
    result = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != DIMENSIONS:
            raise EmbeddingError("embedding_shape", "Vector phải có đủ 1.024 chiều.")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            for value in vector
        ):
            raise EmbeddingError("embedding_invalid", "Vector chứa giá trị không hợp lệ.")
        norm = math.sqrt(math.fsum(float(value) ** 2 for value in vector))
        if not math.isfinite(norm) or abs(norm - 1.0) > 0.001:
            raise EmbeddingError("embedding_norm", "Vector chưa được chuẩn hóa hợp lệ.")
        result.append([float(value) for value in vector])
    return result


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    profile: dict


class F2EmbeddingClient(LocalModelClient):
    module_name = "scripts.run_embedding_model"
    error_type = EmbeddingError
    error_prefix = "embedding"

    def _validate_profile(self, profile: dict, revision: str) -> None:
        if (
            profile.get("model") != MODEL
            or profile.get("revision") != revision
            or profile.get("recipe") != RECIPE
            or profile.get("dimensions") != DIMENSIONS
        ):
            raise EmbeddingError("embedding_profile", "Cấu hình embedding không khớp.")

    async def encode(
        self, texts: list[str], *, revision: str, query: bool = False
    ) -> EmbeddingBatch:
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise EmbeddingError("embedding_revision", "Embedding cần revision cố định của model.")
        if len(texts) > self.batch_size or (query and len(texts) != 1):
            raise EmbeddingError("embedding_input_limit", "Số văn bản trong batch không hợp lệ.")
        if any(
            not isinstance(text, str) or not text.strip() or len(text) > 16_000 for text in texts
        ):
            raise EmbeddingError(
                "embedding_input_limit", "Văn bản embedding không hợp lệ hoặc quá dài."
            )
        result = await self.request(
            revision=revision,
            payload={"operation": "query" if query else "document", "texts": texts}
            if texts
            else None,
        )
        return EmbeddingBatch(
            validate_vectors(result.get("vectors", []), len(texts)), result["profile"]
        )
