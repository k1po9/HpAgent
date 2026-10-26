"""Versioned memory settings shared by HpAgent and the Hindsight entry point.

No SDK, application runtime, environment mutation, or network I/O on import.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PositiveInt = Annotated[int, Field(gt=0)]
IMAGE_DIGEST = "sha256:50311d02b4d5d2b7c5242bd336edf8735df8374eaad496a92f49c83b37d2aa06"
PROMPT_KEYS = {
    "retain_mission", "observations_mission", "retain_custom_instructions",
    "recall_instruction", "reflect_mission",
}


class MemoryConfigError(ValueError):
    """A field-path-only error; never contains a configuration value."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Target(Settings):
    api_version: Literal["1.1.0"] = "1.1.0"
    image_digest: str = IMAGE_DIGEST

    @field_validator('image_digest')
    @classmethod
    def pinned_digest(cls, value):
        if value != IMAGE_DIGEST:
            raise MemoryConfigError('target.image_digest')
        return value


class LabelValue(Settings):
    value: str
    description: str = ""


class LabelMapField(Settings):
    type: Literal["value", "multi-values", "text", "multi-text", "map"] = "text"
    description: str = ""
    values: list[LabelValue] = Field(default_factory=list)
    fields: dict[str, LabelMapField] = Field(default_factory=dict)


class EntityLabel(Settings):
    key: Annotated[str, Field(min_length=1)]
    description: str = ""
    type: Literal["value", "multi-values", "text", "multi-text", "map"] = "value"
    optional: bool = True
    tag: Literal[False] = False
    values: list[LabelValue] = Field(default_factory=list)
    fields: dict[str, LabelMapField] = Field(default_factory=dict)

    @field_validator('tag', mode='before')
    @classmethod
    def no_scope_tags(cls, value):
        if value is not False:
            raise MemoryConfigError('bank.entity_labels.tag')
        return value


class BankSettings(Settings):
    retain_extraction_mode: Literal["concise", "verbose", "custom", "verbatim", "chunks"] | None = "concise"
    retain_chunk_size: PositiveInt | None = 3000
    retain_structured_chunk_size: PositiveInt | None = None
    retain_max_attachments_per_chunk: PositiveInt | None = 8
    entity_labels: list[EntityLabel] | None = None
    entities_allow_free_form: bool | None = True
    enable_observations: bool | None = True
    enable_auto_consolidation: bool | None = True
    consolidation_llm_batch_size: PositiveInt | None = 8
    consolidation_source_facts_max_tokens: int | None = 4096
    consolidation_source_facts_max_tokens_per_observation: int | None = 256
    enable_text_search: bool | None = True
    enable_temporal_retrieval: bool | None = True
    enable_graph_retrieval: bool | None = True
    enable_reranking: bool | None = True

    @model_validator(mode="after")
    def validate_bank(self):
        for key in ("consolidation_source_facts_max_tokens",
                    "consolidation_source_facts_max_tokens_per_observation"):
            if getattr(self, key) not in (None, -1) and getattr(self, key) <= 0:
                raise MemoryConfigError("bank." + key)
        return self


class SourceFacts(Settings):
    max_tokens: PositiveInt = 4096
    max_tokens_per_observation: int = -1

    @model_validator(mode="after")
    def validate_limit(self):
        if self.max_tokens_per_observation != -1 and self.max_tokens_per_observation <= 0:
            raise MemoryConfigError("recall.source_facts.max_tokens_per_observation")
        return self


class MinScores(Settings):
    semantic: float | None = None
    keyword: float | None = None
    reranker: float | None = None
    final: float | None = None

    @model_validator(mode="after")
    def validate_finite(self):
        if any(v is not None and not math.isfinite(v) for v in self.model_dump().values()):
            raise MemoryConfigError("recall.min_scores")
        return self


FactType = Literal["world", "experience", "observation"]


def all_fact_types() -> list[FactType]:
    return ["world", "experience", "observation"]


class RecallSettings(Settings):
    types: list[FactType] = Field(default_factory=all_fact_types)
    budget: Literal["low", "mid", "high"] = "mid"
    max_tokens: PositiveInt = 4096
    top_n: PositiveInt = 5
    prefer_observations: bool = False
    temporal_window: None = None
    min_scores: MinScores = Field(default_factory=MinScores)
    source_facts: SourceFacts = Field(default_factory=SourceFacts)
    include_entities: bool = True
    entities_max_tokens: PositiveInt = 500

    @model_validator(mode="after")
    def nonempty_types(self):
        if not self.types or len(set(self.types)) != len(self.types):
            raise MemoryConfigError("recall.types")
        return self

    def request_options(self) -> dict[str, Any]:
        result = self.model_dump(exclude={"top_n", "source_facts", "include_entities",
                                          "entities_max_tokens", "temporal_window"})
        result["min_scores"] = self.min_scores.model_dump(exclude_none=True) or None
        result["include"] = {
            "source_facts": self.source_facts.model_dump(),
            "entities": {"max_tokens": self.entities_max_tokens} if self.include_entities else None,
        }
        return result


class QuerySettings(Settings):
    mode: Literal["original", "hyde"] = "hyde"
    timestamp_source: Literal["trigger_message"] = "trigger_message"


class Diagnostics(Settings):
    trace: bool = False


class ModelConnection(Settings):
    provider: str
    base_url: str
    api_key_env: str
    model: str
    dimensions: PositiveInt | None = None


class TextSearchSettings(Settings):
    extension: Literal["native", "pgroonga"] = "native"
    native_language: str | None = None

    @model_validator(mode="after")
    def native_only_language(self):
        if self.native_language is not None and (
            self.extension != "native"
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", self.native_language)
        ):
            raise MemoryConfigError("service.text_search.native_language")
        return self

    def resolved(self, environment: Mapping[str, str]) -> dict[str, str | None]:
        backend = environment.get("HINDSIGHT_API_TEXT_SEARCH_EXTENSION")
        if backend not in (None, "", self.extension):
            raise MemoryConfigError("service.text_search.extension_conflict")
        language = environment.get("HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE")
        expected = (self.native_language or "english") if self.extension == "native" else None
        if language and language != expected:
            raise MemoryConfigError("service.text_search.native_language_conflict")
        return {"extension": self.extension, "native_language": expected}


class ServiceSettings(Settings):
    llm: ModelConnection
    retain_llm: ModelConnection | None = None
    consolidation_llm: ModelConnection | None = None
    embeddings: ModelConnection
    reranker: ModelConnection
    retain_max_completion_tokens: PositiveInt = 64000
    text_search: TextSearchSettings = Field(default_factory=TextSearchSettings)

    @model_validator(mode='after')
    def supported_routes(self):
        for role in ('llm', 'retain_llm', 'consolidation_llm', 'embeddings', 'reranker'):
            connection = getattr(self, role)
            if connection is None:
                continue
            expected = 'siliconflow' if role == 'reranker' else 'openai'
            if connection.provider != expected:
                raise MemoryConfigError('service.' + role + '.provider')
            if not re.fullmatch(r'[A-Za-z_]\w*', connection.api_key_env):
                raise MemoryConfigError('service.' + role + '.api_key_env')
            if connection.dimensions is not None and role != 'embeddings':
                raise MemoryConfigError('service.' + role + '.dimensions')
        return self


class InactiveReflect(Settings):
    status: Literal["inactive"] = "inactive"
    disposition_skepticism: Annotated[int, Field(ge=1, le=5)] = 3
    disposition_literalism: Annotated[int, Field(ge=1, le=5)] = 3
    disposition_empathy: Annotated[int, Field(ge=1, le=5)] = 3


class ReferenceOnly(Settings):
    reflect: InactiveReflect = Field(default_factory=InactiveReflect)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def read_yaml(path: str | Path) -> Any:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
            return {} if value is None else value
    except (OSError, yaml.YAMLError):
        raise MemoryConfigError("memory_config_file") from None


def load_memory_prompts(path: str | Path) -> dict[str, str]:
    values = read_yaml(path)
    if (not isinstance(values, dict) or set(values) - PROMPT_KEYS
            or any(not isinstance(v, str) for v in values.values())
            or any(not values.get(k, "").strip() for k in
                   ("retain_mission", "observations_mission", "recall_instruction"))):
        raise MemoryConfigError("prompts.memory")
    return {k: values.get(k, "").strip() for k in PROMPT_KEYS}


class MemoryQualityConfig(Settings):
    schema_version: Literal[1] = 1
    target: Target = Field(default_factory=Target)
    bank: BankSettings = Field(default_factory=BankSettings)
    query: QuerySettings = Field(default_factory=QuerySettings)
    recall: RecallSettings = Field(default_factory=RecallSettings)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
    service: ServiceSettings | None = None
    reference_only: ReferenceOnly = Field(default_factory=ReferenceOnly)

    @field_validator('schema_version', mode='before')
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise MemoryConfigError('schema_version')
        return value

    @classmethod
    def from_yaml(cls, path: str | Path, *, legacy_top_n: Any = None):
        raw = read_yaml(path)
        if isinstance(raw, dict) and legacy_top_n is not None:
            recall = raw.get("recall", {})
            if not isinstance(recall, dict):
                raise MemoryConfigError("recall")
            if "top_n" in recall and recall["top_n"] != legacy_top_n:
                raise MemoryConfigError("agent.recall_top_n conflicts with recall.top_n")
            raw = {**raw, "recall": {**recall, "top_n": legacy_top_n}}
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            paths = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
            raise MemoryConfigError("memory_config: " + ", ".join(paths)) from None

    def bank_updates(self, prompts: Mapping[str, str]) -> dict[str, Any]:
        if self.bank.retain_extraction_mode == "custom" and not prompts.get("retain_custom_instructions"):
            raise MemoryConfigError("prompts.memory.retain_custom_instructions")
        if (self.service and self.bank.retain_chunk_size is not None and self.service.retain_max_completion_tokens <= self.bank.retain_chunk_size):
            raise MemoryConfigError("service.retain_max_completion_tokens <= bank.retain_chunk_size")
        result = self.bank.model_dump(exclude_unset=True)
        for key in ("retain_mission", "observations_mission", "retain_custom_instructions"):
            if key in prompts:
                result[key] = prompts[key] or None
        return result

    def snapshot(self, prompts: Mapping[str, str]) -> dict[str, Any]:
        bank = self.bank_updates(prompts)
        service = self.service.model_dump(exclude={"text_search"}) if self.service else None
        identities = self.model_identities(os.environ)
        text_search = self.text_search_settings().resolved(os.environ)
        target = self.target.model_dump()
        identity = {"service": service, "models": identities,
                    "text_search": text_search, "target": target}
        return {"schema_version": self.schema_version, "bank": bank,
                "config_hash": fingerprint({"bank": bank, **identity}),
                "prompt_hash": fingerprint(dict(prompts)),
                "service_hash": fingerprint(identity),
                "model_identities": identities, "text_search": text_search,
                "target": target, "service_remote_verified": False}

    def text_search_settings(self) -> TextSearchSettings:
        return self.service.text_search if self.service else TextSearchSettings()

    def text_search_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        resolved = self.text_search_settings().resolved(environment)
        output = {"HINDSIGHT_API_TEXT_SEARCH_EXTENSION": str(resolved["extension"])}
        if resolved["native_language"] is not None:
            output["HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE"] = str(resolved["native_language"])
        return output

    def model_identities(self, environment: Mapping[str, str]) -> dict[str, Any]:
        """Local model choices only; no addresses or credential values."""
        if self.service is None:
            return {}
        result = {}
        prefixes = {"llm": "LLM", "retain_llm": "RETAIN_LLM",
                    "consolidation_llm": "CONSOLIDATION_LLM",
                    "embeddings": "EMBEDDINGS_OPENAI", "reranker": "RERANKER_SILICONFLOW"}
        for role, prefix in prefixes.items():
            connection = getattr(self.service, role)
            inherited = connection is None
            if inherited:
                connection = self.service.llm
            def resolve(raw: str) -> str:
                def substitute(match: re.Match[str]) -> str:
                    return environment.get(match.group(1), match.group(0))
                return re.sub(r"\$\{([A-Za-z_]\w*)\}", substitute, raw)
            raw = environment.get("HINDSIGHT_API_" + prefix + "_MODEL")
            if not raw:
                raw = ((environment.get("HINDSIGHT_API_LLM_MODEL") if inherited else None)
                       or connection.model)
            model = resolve(raw)
            provider_prefix = {"embeddings": "EMBEDDINGS", "reranker": "RERANKER"}.get(role, prefix)
            provider = (environment.get("HINDSIGHT_API_" + provider_prefix + "_PROVIDER")
                        or (environment.get("HINDSIGHT_API_LLM_PROVIDER") if inherited else None)
                        or connection.provider)
            address = (environment.get("HINDSIGHT_API_" + prefix + "_BASE_URL")
                       or (environment.get("HINDSIGHT_API_LLM_BASE_URL") if inherited else None)
                       or connection.base_url)
            result[role] = {"provider": provider, "model": model,
                            "resolved": "${" not in model,
                            "endpoint_hash": fingerprint(resolve(address))}
        return result

    def service_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        output = self.text_search_environment(environment)
        if self.service is None:
            return output

        def expand(value: str, field: str) -> str:
            def replace(match):
                resolved = environment.get(match[1], "")
                if not resolved or "${" in resolved:
                    raise MemoryConfigError(field)
                return resolved
            result = re.sub(r"\$\{([A-Za-z_]\w*)\}", replace, value)
            if not result or "${" in result:
                raise MemoryConfigError(field)
            return result

        for role, prefix, allowed in (
            ("llm", "LLM", "openai"), ("retain_llm", "RETAIN_LLM", "openai"),
            ("consolidation_llm", "CONSOLIDATION_LLM", "openai"),
            ("embeddings", "EMBEDDINGS_OPENAI", "openai"),
            ("reranker", "RERANKER_SILICONFLOW", "siliconflow"),
        ):
            connection = getattr(self.service, role)
            if connection is None:
                continue
            provider_key = "HINDSIGHT_API_" + (
                "EMBEDDINGS" if role == "embeddings" else "RERANKER" if role == "reranker" else prefix
            ) + "_PROVIDER"
            provider = environment.get(provider_key) or connection.provider
            if provider != allowed:
                raise MemoryConfigError("service." + role + ".provider")
            output[provider_key] = provider
            for suffix, raw in (("BASE_URL", connection.base_url), ("MODEL", connection.model)):
                name = "HINDSIGHT_API_" + prefix + "_" + suffix
                output[name] = expand(environment.get(name) or raw, "service." + role + "." + suffix.lower())
            key_name = "HINDSIGHT_API_" + prefix + "_API_KEY"
            secret = environment.get(key_name) or environment.get(connection.api_key_env, "")
            if not secret or "${" in secret:
                raise MemoryConfigError("service." + role + ".api_key_env")
            output[key_name] = secret
            if connection.dimensions is not None:
                if role != "embeddings":
                    raise MemoryConfigError("service." + role + ".dimensions")
                output["HINDSIGHT_API_EMBEDDINGS_OPENAI_DIMENSIONS"] = str(connection.dimensions)
        output["HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS"] = environment.get(
            "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS") or str(self.service.retain_max_completion_tokens)
        if int(output["HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS"]) <= (self.bank.retain_chunk_size or 3000):
            raise MemoryConfigError("service.retain_max_completion_tokens")
        return output
