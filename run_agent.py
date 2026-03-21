"""Run stock-forecast-agent and validate end-to-end OpenAPI tool flow.

Usage:
    python run_agent.py
    python run_agent.py "Analyze TSLA"
    python run_agent.py --tests
    python run_agent.py --stream "Analyze NVDA short term"
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

try:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
except Exception:
    AIProjectClient = None
    DefaultAzureCredential = None

PROJECT_ENDPOINT = os.getenv(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper",
)
PROJECT_API_KEY = os.getenv("AZURE_AI_PROJECT_API_KEY", "")
AGENT_NAME = os.getenv("AZURE_AI_AGENT_NAME", "stock-forecast-agent")


@dataclass
class AgentRunResult:
    prompt: str
    output_text: str
    tool_calls: list[str]


def create_openai_client() -> OpenAI:
    if PROJECT_API_KEY:
        return OpenAI(
            api_key=PROJECT_API_KEY,
            base_url=f"{PROJECT_ENDPOINT.rstrip('/')}/openai/v1",
        )

    if AIProjectClient is None or DefaultAzureCredential is None:
        raise RuntimeError(
            "Install azure-ai-projects and azure-identity to use DefaultAzureCredential mode, "
            "or set AZURE_AI_PROJECT_API_KEY."
        )

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
    return project_client.get_openai_client()


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def extract_tool_call_names(response: Any) -> list[str]:
    calls: list[str] = []
    output_items = _safe_get(response, "output", []) or []

    for item in output_items:
        item_type = str(_safe_get(item, "type", ""))
        if "function_call" not in item_type and "tool_call" not in item_type:
            continue

        name = _safe_get(item, "name")
        if not name:
            function_data = _safe_get(item, "function", {})
            name = _safe_get(function_data, "name")

        if name:
            calls.append(str(name))

    return calls


def response_text(response: Any) -> str:
    text = _safe_get(response, "output_text", "")
    if isinstance(text, str) and text.strip():
        return text.strip()

    output_items = _safe_get(response, "output", []) or []
    chunks: list[str] = []
    for item in output_items:
        if _safe_get(item, "type") != "message":
            continue
        content = _safe_get(item, "content", []) or []
        for block in content:
            if _safe_get(block, "type") == "output_text":
                value = _safe_get(block, "text", "")
                if value:
                    chunks.append(str(value))
    return "\n".join(chunks).strip()


def run_agent_prompt(prompt: str, stream: bool = False, retries: int = 2) -> AgentRunResult:
    client = create_openai_client()
    conversation = client.conversations.create(
        items=[{"type": "message", "role": "user", "content": prompt}],
    )

    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            if stream:
                print("Streaming flag enabled; using final-response mode for agent_reference compatibility.")

            final_response = client.responses.create(
                conversation=conversation.id,
                extra_body={"agent_reference": {"type": "agent_reference", "name": AGENT_NAME}},
                input="",
            )

            return AgentRunResult(
                prompt=prompt,
                output_text=response_text(final_response),
                tool_calls=extract_tool_call_names(final_response),
            )
        except Exception as exc:
            last_error = exc
            if attempt > retries:
                break
            sleep_seconds = attempt * 2
            print(f"Retrying after error (attempt {attempt}/{retries + 1}): {exc}")
            time.sleep(sleep_seconds)

    raise RuntimeError(f"Agent call failed after retries: {last_error}")


def verify_tool_order(tool_calls: list[str]) -> bool:
    lowered = [name.lower() for name in tool_calls]
    try:
        idx_price = next(i for i, value in enumerate(lowered) if "price_history" in value)
        idx_tech = next(i for i, value in enumerate(lowered) if "technical" in value)
        idx_news = next(i for i, value in enumerate(lowered) if "news_sentiment" in value)
    except StopIteration:
        return False
    return idx_price < idx_tech < idx_news


def run_tests(stream: bool = False) -> None:
    cases = [
        "Analyze TSLA",
        "Analyze NVDA short term",
        "Analyze BADTICKERZZZZ",
    ]

    print("Running end-to-end test cases...\n")
    results: list[dict[str, Any]] = []

    for prompt in cases:
        result = run_agent_prompt(prompt=prompt, stream=stream)
        order_ok = verify_tool_order(result.tool_calls)
        results.append(
            {
                "prompt": prompt,
                "tool_calls": result.tool_calls,
                "tool_order_ok": order_ok,
                "output_excerpt": result.output_text[:600],
            }
        )
        print(f"Prompt: {prompt}")
        print(f"Tool calls: {result.tool_calls}")
        print(f"Order check (price_history -> technicals -> news_sentiment): {order_ok}")
        print(f"Output excerpt:\n{result.output_text[:500]}\n")

    print("JSON summary:")
    print(json.dumps(results, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stock-forecast-agent via Azure AI Foundry responses.create")
    parser.add_argument("prompt", nargs="?", default="Analyze AAPL stock", help="Prompt sent to the agent")
    parser.add_argument("--tests", action="store_true", help="Run required end-to-end test cases")
    parser.add_argument("--stream", action="store_true", help="Enable streaming response output when available")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.tests:
        run_tests(stream=args.stream)
        return

    result = run_agent_prompt(prompt=args.prompt, stream=args.stream)
    print("Tool calls:", result.tool_calls)
    print("\nAgent response:\n")
    print(result.output_text)


if __name__ == "__main__":
    main()