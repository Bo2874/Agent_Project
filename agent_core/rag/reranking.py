"""Qwen relevance scoring over bounded passages, using the shared local-model transport."""

import math
import re
from dataclasses import dataclass

from agent_core.rag.local_models import LocalModelClient, LocalModelError

MODEL = "Qwen/Qwen3-Reranker-0.6B"
RECIPE = "qwen3-yes-no-document-qa-v1"
INSTRUCTION = "Given a question, retrieve relevant document passages that answer the question"
MAX_PAIR_TOKENS = 2048
PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
    'and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


class RerankingError(LocalModelError):
    pass


@dataclass(frozen=True)
class RerankingBatch:
    scores: list[float]
    profile: dict


class QwenRerankerClient(LocalModelClient):
    module_name = "scripts.run_reranker_model"
    error_type = RerankingError
    error_prefix = "reranker"

    def _validate_profile(self, profile: dict, revision: str) -> None:
        resolved = profile.get("revision", "")
        if (
            profile.get("model") != MODEL
            or profile.get("recipe") != RECIPE
            or profile.get("max_pair_tokens") != MAX_PAIR_TOKENS
            or not re.fullmatch(r"[0-9a-f]{40}", resolved)
            or (re.fullmatch(r"[0-9a-f]{40}", revision) and revision != resolved)
        ):
            raise RerankingError("reranker_profile", "Cấu hình reranker không khớp.")

    async def score(self, query: str, passages: list[str], *, revision: str) -> RerankingBatch:
        if (
            not 1 <= len(passages) <= 20
            or not query.strip()
            or len(query) > 4000
            or any(not text.strip() or len(text) > 16_000 for text in passages)
            or not 1 <= len(revision) <= 160
        ):
            raise RerankingError("reranker_input_limit", "Đầu vào reranker không hợp lệ.")
        result = await self.request(
            revision=revision,
            payload={
                "operation": "score",
                "query": query,
                "passages": passages,
                "batch_size": self.batch_size,
            },
        )
        scores = result.get("scores")
        if (
            not isinstance(scores, list)
            or len(scores) != len(passages)
            or any(
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not 0 <= score <= 1
                for score in scores
            )
        ):
            raise RerankingError("reranker_scores", "Điểm rerank không hợp lệ.")
        return RerankingBatch([float(score) for score in scores], result["profile"])
