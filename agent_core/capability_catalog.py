"""Schema-free, immutable metadata snapshots for discovery and lexical routing."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType

from agent_core.contracts import CapabilityDescriptor


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value))


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    descriptor: CapabilityDescriptor
    binding_name: str
    keywords: frozenset[str] = frozenset()
    always_load: bool = False


class CapabilityCatalog:
    """An already-authorized view; metadata never grants execution authority.

    All indexes contain metadata only. No schema loaders or tool handlers are called here.
    Instantiate a new snapshot after registration/policy changes; keep a snapshot for a run.
    """

    def __init__(self, records: tuple[CapabilityRecord, ...], *, revision: int = 0) -> None:
        ordered = tuple(sorted(records, key=lambda r: r.binding_name))
        if len({r.binding_name for r in ordered}) != len(ordered) or len(
            {r.descriptor.capability_id for r in ordered}
        ) != len(ordered):
            raise ValueError("Duplicate capability identity or binding")
        by_word: dict[str, set[str]] = {}
        by_filter: dict[tuple[str, str], set[str]] = {}
        name_words, description_words, phrases = {}, {}, {}
        for record in ordered:
            cap, name = record.descriptor, record.binding_name
            names = frozenset(normalize(f"{name} {cap.name}").split())
            descriptions = frozenset(normalize(cap.description).split())
            name_words[name], description_words[name] = names, descriptions
            for word in names | descriptions:
                by_word.setdefault(word, set()).add(name)
            for keyword in record.keywords:
                phrase = normalize(keyword)
                if phrase:
                    phrases.setdefault(phrase, set()).add(name)
            for key, value in (("kind", cap.kind), ("plugin_id", cap.plugin_id)):
                if value is not None:
                    by_filter.setdefault((key, value), set()).add(name)
            for tag in cap.tags:
                by_filter.setdefault(("tag", tag), set()).add(name)
                for word in normalize(tag).split():
                    by_word.setdefault(word, set()).add(name)
        payload = [
            {
                "descriptor": r.descriptor.model_dump(mode="json"),
                "binding_name": r.binding_name,
                "keywords": sorted(r.keywords),
                "always_load": r.always_load,
            }
            for r in ordered
        ]
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._records = MappingProxyType({r.binding_name: r for r in ordered})
        self._words = MappingProxyType({k: frozenset(v) for k, v in by_word.items()})
        self._filters = MappingProxyType({k: frozenset(v) for k, v in by_filter.items()})
        self._names = MappingProxyType(name_words)
        self._descriptions = MappingProxyType(description_words)
        self._phrases = MappingProxyType({k: frozenset(v) for k, v in phrases.items()})
        self._always = frozenset(r.binding_name for r in ordered if r.always_load)
        self._version = f"cc1:{revision}:{digest}"

    @property
    def version(self) -> str:
        return self._version

    @property
    def records(self) -> tuple[CapabilityRecord, ...]:
        return tuple(self._records.values())

    def search(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        plugin_id: str | None = None,
        tags: tuple[str, ...] = (),
        limit: int = 100,
    ) -> tuple[CapabilityRecord, ...]:
        if not 1 <= limit <= 4096 or len(query) > 32768:
            raise ValueError("Capability search bounds exceeded")
        normalized = normalize(query)
        words = set(normalized.split())
        scores: dict[str, int] = {}
        candidates = set(self._always)
        for word in words:
            candidates.update(self._words.get(word, ()))
        for phrase, names in self._phrases.items():
            if phrase in normalized:
                candidates.update(names)
                for name in names:
                    scores[name] = scores.get(name, 0) + 20 + len(phrase.split())
        if not normalized:
            candidates = set(self._records)
        for key, value in (("kind", kind), ("plugin_id", plugin_id)):
            if value is not None:
                candidates.intersection_update(self._filters.get((key, value), ()))
        for tag in tags:
            candidates.intersection_update(self._filters.get(("tag", tag), ()))
        for name in candidates:
            scores[name] = (
                scores.get(name, 0)
                + len(words & self._names[name]) * 6
                + len(words & self._descriptions[name])
                + (1_000_000 if name in self._always else 0)
            )
        return tuple(
            self._records[name]
            for name in sorted(candidates, key=lambda name: (-scores.get(name, 0), name))[:limit]
        )
