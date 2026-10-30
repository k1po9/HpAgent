"""Structured report synthesis over the existing model ResourcePool."""

from __future__ import annotations

import json
from typing import Any

from application.prompt_catalog import agent_prompt
from research_domain.models import ClaimDraft, ResearchReport
from research_domain.policy import ResearchPolicy


class SynthesisValidationError(ValueError):
    """Stable semantic failure of a received response; never a retry signal."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ResourcePoolResearchSynthesizer:
    def __init__(self, resource_pool: Any, *, model_selector: str = "chat") -> None:
        self.resource_pool = resource_pool
        self.model_selector = model_selector

    @staticmethod
    def _decode_object(value: str) -> dict[str, Any]:
        """Accept a JSON object with optional model prose or Markdown fences."""
        decoder = json.JSONDecoder()
        for index, character in enumerate(value):
            if character != "{":
                continue
            try:
                payload, _end = decoder.raw_decode(value[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise SynthesisValidationError("invalid_json", "research synthesis did not return a complete JSON object")

    async def request(self, objective: str, evidence: list[dict[str, object]], *, policy: ResearchPolicy | None = None) -> dict[str, Any]:
        response = await self.resource_pool.generate(
            messages=[
                {
                    "role": "system",
                    "content": policy.synthesis_prompt if policy is not None else agent_prompt("research"),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"objective": objective, "evidence": [
                            {**item, "source_locator": {k: v for k, v in dict(item.get("source_locator") or {}).items() if k != "excerpt"}}
                            for item in evidence
                        ]},
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            model_selector=policy.model_selector if policy is not None else self.model_selector,
            max_tokens=policy.max_tokens if policy is not None else ResearchPolicy.max_tokens,
        )
        return {
            "content": str(getattr(response, "content", "") or ""),
            "snapshot_id": getattr(response, "snapshot_id", None),
            "model_call_id": getattr(response, "model_call_id", None),
            "usage": dict(getattr(response, "usage", {}) or {}),
            "parse_status": "pending",
        }

    @classmethod
    def parse(cls, response: dict[str, Any], objective: str, evidence: list[dict[str, object]]) -> ResearchReport:
        evidence_ids = {str(item["evidence_id"]) for item in evidence}
        payload = cls._decode_object(response["content"].strip())
        if (not isinstance(payload.get("title"), str) or not payload["title"].strip()
                or ("report_markdown" in payload and (not isinstance(payload["report_markdown"], str) or not payload["report_markdown"].strip()))
                or not isinstance(payload.get("claims"), list)):
            raise SynthesisValidationError("invalid_fields", "research synthesis fields have invalid shape")
        if not payload["claims"]:
            raise SynthesisValidationError("missing_claims", "research synthesis requires cited claims")
        if len(payload["claims"]) > 16:
            raise SynthesisValidationError("invalid_fields", "research synthesis exceeds claim bound")
        claims: list[ClaimDraft] = []
        for raw in payload["claims"]:
            if (not isinstance(raw, dict) or not isinstance(raw.get("statement"), str)
                    or not raw["statement"].strip() or raw.get("importance", "normal") not in ("normal", "important")
                    or not isinstance(raw.get("evidence_ids"), list)
                    or any(not isinstance(item, str) for item in raw["evidence_ids"])):
                raise SynthesisValidationError("invalid_fields", "research synthesis claim has invalid shape")
            cited = tuple(dict.fromkeys(raw["evidence_ids"]))
            if not cited:
                raise SynthesisValidationError("missing_citation", "research synthesis claim has no EvidenceItem")
            if not set(cited).issubset(evidence_ids):
                raise SynthesisValidationError("unknown_evidence", "research synthesis cited an unknown EvidenceItem")
            claims.append(ClaimDraft(statement=raw["statement"], importance=raw.get("importance", "normal"), evidence_ids=cited))
        from research_domain.excerpts import missing_concepts

        missing = missing_concepts(objective, [claim.statement for claim in claims])
        if missing:
            raise SynthesisValidationError("missing_coverage", "research claims omit requested concepts: " + ", ".join(missing))
        # Only registered cited claims enter the deliverable. The complete raw
        # response remains in the existing operation receipt for audit/replay.
        # Validating IDs is structural grounding, not an entailment proof.
        title = payload["title"][:200].replace("\n", " ").replace("\r", " ")
        sources = {str(item["evidence_id"]): item for item in evidence}
        lines = ["# " + title, ""]
        for claim in claims:
            statement = claim.statement.strip()
            labels = " ".join(f"[E:{identity}]" for identity in claim.evidence_ids)
            lines.extend(["- " + statement + " " + labels, ""])
        lines.extend(["## Sources", ""])
        for identity in dict.fromkeys(identity for claim in claims for identity in claim.evidence_ids):
            locator = dict(sources[identity].get("source_locator") or {})
            uri = locator.get("canonical_uri")
            lines.append(f"- [E:{identity}]" + (" " + str(uri) if uri else ""))
        return ResearchReport(title=title, report_markdown="\n".join(lines), claims=tuple(claims))

    async def synthesize(self, objective: str, evidence: list[dict[str, object]]) -> ResearchReport:
        return self.parse(await self.request(objective, evidence), objective, evidence)
