"""Grok Part Advisor — AI-powered part selection and recommendation.

Uses Grok-3 (via OpenAI-compatible API) to analyse ESN search results
and recommend the best part match, or annotate all options with notes.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GrokRecommendation:
    """Grok's assessment for a single part result."""
    part_number: str = ""
    confidence: str = ""          # "high", "medium", "low"
    recommended: bool = False
    reason: str = ""
    warnings: str = ""


def analyse_esn_results(
    esn: str,
    keyword: str,
    manufacturer: str,
    results: list[dict],
) -> list[GrokRecommendation]:
    """Send ESN search results to Grok-3 for analysis.

    Each result dict should have keys: part_number, description,
    kit_number, kit_description.

    Returns a GrokRecommendation per result, with the best match
    flagged as recommended=True.
    """
    api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("No XAI_API_KEY set — skipping Grok analysis")
        return [GrokRecommendation(part_number=r.get("part_number", ""))
                for r in results]

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    # Build the parts list for the prompt
    parts_text = ""
    for i, r in enumerate(results, 1):
        parts_text += (
            f"  {i}. Part Number: {r.get('part_number', 'N/A')}\n"
            f"     Part Description: {r.get('description', 'N/A')}\n"
            f"     Kit Number: {r.get('kit_number', 'N/A')}\n"
            f"     Kit Description: {r.get('kit_description', 'N/A')}\n\n"
        )

    system = (
        "You are a senior diesel engine parts specialist at a diesel parts "
        "dealership. You help technicians select the correct part or kit for a "
        "specific engine based on ESN lookups. Respond ONLY with a JSON array — "
        "no markdown fences, no commentary."
    )

    prompt = f"""A customer needs parts for this engine:
  Manufacturer: {manufacturer}
  ESN: {esn}
  Customer requested: "{keyword}"

The Cummins parts catalog returned these results:
{parts_text}

For EACH part above, return a JSON object:
{{
  "part_number": "<the part number>",
  "confidence": "high" | "medium" | "low",
  "recommended": true | false,
  "reason": "<1-2 sentence explanation of what this kit/part is and who it's for>",
  "warnings": "<any cautions — superseded, special tooling, core charge, etc.>"
}}

Rules:
- Mark exactly ONE part as "recommended": true (the best match for the customer's request)
- If the customer asked for "Overhaul Kit", prefer the STANDARD/CLASSIC kit over PRO/ELITE unless context says otherwise
- Consider: CLASSIC = budget rebuild, PRO = performance, ELITE = premium
- If you can't confidently recommend one, pick the most common/standard option with confidence "medium"

Return a JSON array, one object per part. No extra text."""

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        content = response.choices[0].message.content.strip()
        # Strip markdown fences
        if content.startswith("```"):
            content = re.sub(r"^```[a-z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        parsed = json.loads(content)
        if not isinstance(parsed, list):
            parsed = [parsed]

        recs = []
        for item in parsed:
            recs.append(GrokRecommendation(
                part_number=str(item.get("part_number", "")),
                confidence=item.get("confidence", "low"),
                recommended=bool(item.get("recommended", False)),
                reason=item.get("reason", ""),
                warnings=item.get("warnings", ""),
            ))
        return recs

    except Exception as exc:
        logger.exception("Grok analysis failed")
        return [GrokRecommendation(part_number=r.get("part_number", ""))
                for r in results]


def get_grok_available() -> bool:
    """Check if Grok API key is configured."""
    return bool(os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY"))
