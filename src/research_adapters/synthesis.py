"""Structured report synthesis over the existing model ResourcePool."""

from __future__ import annotations

import json
from typing import Any

from research_domain.models import ClaimDraft, ResearchReport


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
        raise ValueError("research synthesis did not return a complete JSON object")

    async def synthesize(self, objective: str, evidence: list[dict[str, object]]) -> ResearchReport:
        evidence_ids = {str(item["evidence_id"]) for item in evidence}
        response = await self.resource_pool.generate(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Produce a research report strictly as one JSON object with keys title, "
                        "report_markdown, claims. Each claim has statement, importance "
                        "(normal|important), and evidence_ids. Cite only supplied evidence IDs; "
                        "do not put unsupported claims in the report. Do not emit analysis, "
                        "Markdown fences, or any text outside the JSON. Keep the report concise "
                        "and use at most 8 claims."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"objective": objective, "evidence": evidence},
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            model_selector=self.model_selector,
            max_tokens=4096,
        )
        value = str(getattr(response, "content", "") or "").strip()
        payload = self._decode_object(value)
        claims: list[ClaimDraft] = []
        for raw in payload.get("claims", []):
            cited = tuple(str(item) for item in raw.get("evidence_ids", []))
            if not cited or not set(cited).issubset(evidence_ids):
                raise ValueError("research synthesis cited an unknown EvidenceItem")
            claims.append(
                ClaimDraft(
                    statement=str(raw.get("statement") or ""),
                    importance=str(raw.get("importance") or "normal"),
                    evidence_ids=cited,
                )
            )
        return ResearchReport(
            title=str(payload.get("title") or objective)[:200],
            report_markdown=str(payload.get("report_markdown") or ""),
            claims=tuple(claims),
        )
