"""Deterministic fusion and source-preserving context packing, independent of storage."""

import json
from dataclasses import dataclass, field
from uuid import UUID

from agent_core.context_budget import ContextBlock, measure_context_blocks
from agent_core.contracts.retrieval import RetrievalResult

FUSION_RECIPE = "rrf60-equal-20x20-top10-v2"
PACKING_RECIPE = "ranked-round-robin-5x3-budget4000-v2"


def reciprocal_rank_fusion(*rankings: list[RetrievalResult]) -> list[RetrievalResult]:
    scores, hits = {}, {}
    for ranking in rankings:
        seen = set()
        for rank, hit in enumerate(ranking, 1):
            key = (hit.version_id, hit.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            hits.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
    ordered = sorted(hits, key=lambda key: (-scores[key], str(key[0]), str(key[1])))[:10]
    return [
        hits[key].model_copy(update={"score": scores[key], "score_kind": "fusion"})
        for key in ordered
    ]


def evidence_block(hit: RetrievalResult) -> ContextBlock:
    # Preserve the complete source and its offsets. Escaping belongs to the context serializer.
    payload = hit.model_dump(mode="json", exclude={"score", "score_kind"})
    return ContextBlock(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        source=hit.citation_id,
    )


@dataclass(frozen=True)
class PackedEvidence:
    items: list[RetrievalResult]
    blocks: list[ContextBlock]
    estimated_tokens: int
    budget_skipped: int
    query_contributions: list[int] = field(default_factory=list)
    chunk_queries: dict[UUID, list[int]] = field(default_factory=dict)
    duplicate_skipped: int = 0
    content_duplicate_skipped: int = 0


def pack_evidence(hits: list[RetrievalResult], *, token_budget: int) -> PackedEvidence:
    # Search API keeps contract-valid opaque source IDs; QA uses final S1/S2 labels.
    return select_ranked_evidence([hits], token_budget=token_budget, label_sources=False)


def select_ranked_evidence(
    rankings: list[list[RetrievalResult]], *, token_budget: int, label_sources: bool = True
) -> PackedEvidence:
    """Consume each ranking in order, with duplicates never spending a query's turn.

    Scores are local to a query. Do not compare or re-sort them across queries.
    Only exact text duplicates within the same version/section are collapsed;
    partially overlapping passages retain their original text and provenance.
    """
    if token_budget < 0:
        raise ValueError("Evidence budget cannot be negative")
    if not 1 <= len(rankings) <= 3:
        raise ValueError("Expected one to three query rankings")
    token_budget = min(token_budget, 4000)
    rankings = [hits[:10] for hits in rankings]
    items, blocks, skipped = [], [], 0
    positions, contributions = [0] * len(rankings), [0] * len(rankings)
    seen, seen_content = set(), set()
    duplicate_skipped, content_duplicate_skipped = 0, 0
    while len(items) < 15:
        added = False
        for index, hits in enumerate(rankings):
            if contributions[index] >= 5:
                continue
            while positions[index] < len(hits):
                hit = hits[positions[index]]
                positions[index] += 1
                if hit.chunk_id in seen:
                    duplicate_skipped += 1
                    continue
                # Preserve indentation/table spacing and distinct section context.
                content_key = (hit.version_id, hit.section, hit.text.replace("\r\n", "\n"))
                if content_key in seen_content:
                    content_duplicate_skipped += 1
                    continue
                # Measure the exact final label/metadata, not the longer retrieval label.
                if label_sources:
                    hit = hit.model_copy(update={"citation_id": f"S{len(items) + 1}"})
                block = evidence_block(hit)
                if measure_context_blocks("rag", [*blocks, block]) > token_budget:
                    skipped += 1
                    continue
                items.append(hit)
                blocks.append(block)
                seen.add(hit.chunk_id)
                seen_content.add(content_key)
                contributions[index] += 1
                added = True
                break
            if len(items) == 15:
                break
        if not added:
            break
    return PackedEvidence(
        items=items,
        blocks=blocks,
        estimated_tokens=measure_context_blocks("rag", blocks),
        budget_skipped=skipped,
        query_contributions=contributions,
        chunk_queries={
            hit.chunk_id: [
                index + 1
                for index, hits in enumerate(rankings)
                if any(candidate.chunk_id == hit.chunk_id for candidate in hits)
            ]
            for hit in items
        },
        duplicate_skipped=duplicate_skipped,
        content_duplicate_skipped=content_duplicate_skipped,
    )
