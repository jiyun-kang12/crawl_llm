from __future__ import annotations

import json
import os
from typing import Any


def _anthropic_classify(
    system: str, user: str, schema: dict, schema_name: str, model: str | None
) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    tool = {
        "name": schema_name,
        "description": f"Return {schema_name} as structured data matching the schema.",
        "input_schema": schema,
    }
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[tool],
        tool_choice={"type": "tool", "name": schema_name},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Anthropic 응답에 tool_use 블록이 없습니다")


def _openai_classify(
    system: str, user: str, schema: dict, schema_name: str, model: str | None
) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        },
        temperature=0,
    )
    content = resp.choices[0].message.content
    return json.loads(content)


def classify_json(
    system: str,
    user: str,
    schema: dict,
    schema_name: str,
    provider: str,
    model: str | None = None,
) -> dict[str, Any]:
    """구조화된 JSON 스키마로 LLM 분류 결과를 받아온다. provider: 'anthropic' | 'openai'."""
    if provider == "anthropic":
        return _anthropic_classify(system, user, schema, schema_name, model)
    if provider == "openai":
        return _openai_classify(system, user, schema, schema_name, model)
    raise ValueError(f"알 수 없는 LLM_PROVIDER: {provider!r} (anthropic | openai)")
