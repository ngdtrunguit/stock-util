"""Run stock-forecast-agent and validate end-to-end OpenAPI tool flow.

Uses the Azure AI Foundry /openai/v1/responses endpoint with the agent definition
(model, instructions, tools) fetched at runtime from the stored PromptAgent via
AIProjectClient.agents.get().  This avoids the Applications/deployment layer.

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
from typing import Any
from dataclasses import dataclass, field

from openai import OpenAI

try:
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from azure.ai.projects import AIProjectClient
except Exception:
    DefaultAzureCredential = None
    get_bearer_token_provider = None
    AIProjectClient = None  # type: ignore[assignment,misc]

PROJECT_ENDPOINT = os.getenv(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper",
)
PROJECT_API_KEY = os.getenv("AZURE_AI_PROJECT_API_KEY", "")
AGENT_NAME = os.getenv("AZURE_AI_AGENT_NAME", "stock-forecast-agent")
DEFAULT_ANALYSIS_TICKER = "AAPL"
DEFAULT_ANALYSIS_DAYS = 180
DEFAULT_TEST_PROMPTS = (
    "Analyze TSLA",
    "Analyze NVDA short term",
    "Analyze BADTICKERZZZZ",
)
DEFAULT_EXPECTED_INVALID_TICKERS = ("BADTICKERZZZZ",)


@dataclass
class AgentRunResult:
    prompt: str
    output_text: str
    tool_calls: list[str]


@dataclass
class AgentDefinition:
    model: str
    instructions: str
    tools: list[Any] = field(default_factory=list)
    tool_choice: str = "auto"


def prompt_requires_tools(prompt: str) -> bool:
    normalized = prompt.strip().lower()
    return normalized.startswith("analyze ") and any(char.isalpha() for char in normalized)


def resolve_tool_choice(prompt: str, default_tool_choice: str) -> str:
    if prompt_requires_tools(prompt):
        return "required"
    return default_tool_choice or "auto"


def _allow_interactive_browser() -> bool:
    return os.getenv("AZURE_AI_ALLOW_INTERACTIVE_BROWSER", "").strip().lower() in {"1", "true", "yes", "on"}


def _split_nonempty_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _normalize_ticker(value: str) -> str:
    return value.strip().upper()


def _looks_like_ticker_symbol(value: str) -> bool:
    candidate = _normalize_ticker(value)
    allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")

    if not candidate or len(candidate) > 10 or " " in candidate:
        return False

    return any(char.isalpha() for char in candidate) and all(char in allowed_chars for char in candidate)


def build_analysis_prompt(ticker: str, days: int = DEFAULT_ANALYSIS_DAYS, context: str = "") -> str:
    normalized_ticker = _normalize_ticker(ticker)
    extra_context = context.strip()

    if not normalized_ticker:
        raise ValueError("Ticker cannot be empty.")
    if days < 1:
        raise ValueError("days must be greater than 0.")

    prompt = f"Analyze {normalized_ticker} with {days}-day context."
    if extra_context:
        prompt = f"{prompt} {extra_context}"
    return prompt


def resolve_prompt(prompt: str | None, ticker: str | None = None, days: int = DEFAULT_ANALYSIS_DAYS, context: str = "") -> str:
    if ticker:
        return build_analysis_prompt(ticker=ticker, days=days, context=context)

    candidate = (prompt or "").strip()
    if not candidate:
        return build_analysis_prompt(ticker=DEFAULT_ANALYSIS_TICKER, days=days, context=context)
    if _looks_like_ticker_symbol(candidate):
        return build_analysis_prompt(ticker=candidate, days=days, context=context)
    return candidate


def resolve_test_prompts(test_prompts: list[str] | None = None) -> list[str]:
    if test_prompts:
        resolved = [prompt.strip() for prompt in test_prompts if prompt.strip()]
        if resolved:
            return resolved

    env_prompts = _split_nonempty_lines(os.getenv("AZURE_AI_TEST_PROMPTS", ""))
    if env_prompts:
        return env_prompts

    return list(DEFAULT_TEST_PROMPTS)


def resolve_expected_invalid_tickers(tickers: list[str] | None = None) -> set[str]:
    values = tickers or _split_nonempty_lines(os.getenv("AZURE_AI_EXPECTED_INVALID_TICKERS", "")) or list(DEFAULT_EXPECTED_INVALID_TICKERS)
    return {_normalize_ticker(value) for value in values if _normalize_ticker(value)}


def prompt_mentions_ticker(prompt: str, tickers: set[str]) -> bool:
    normalized_prompt = prompt.upper()
    return any(ticker in normalized_prompt for ticker in tickers)


def _build_direct_base_url(project_endpoint: str) -> str:
    """Return the /openai/v1 base URL for the Foundry project's direct model endpoint."""
    return f"{project_endpoint.rstrip('/')}/openai/v1"


def _safe_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_credential() -> Any:
    if DefaultAzureCredential is None:
        raise RuntimeError(
            "Install azure-identity to invoke Azure AI Foundry agents, then authenticate with "
            "DefaultAzureCredential (for example via az login or azure/login in GitHub Actions)."
        )
    return DefaultAzureCredential(
        exclude_interactive_browser_credential=not _allow_interactive_browser()
    )


def get_agent_definition(agent_name: str) -> AgentDefinition:
    """Fetch the agent definition (model, instructions, tools) from Azure AI Foundry."""
    if AIProjectClient is None:
        raise RuntimeError("azure-ai-projects is not installed.")

    credential = _get_credential()
    with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential) as project_client:
        agent = project_client.agents.get(agent_name)

    agent_dict = agent if isinstance(agent, dict) else (agent.as_dict() if hasattr(agent, 'as_dict') else vars(agent))
    versions = _safe_attr(agent_dict, "versions", {})
    latest = _safe_attr(versions, "latest", {}) if isinstance(versions, dict) else {}
    definition = _safe_attr(latest, "definition", {})

    model = str(_safe_attr(definition, "model", "") or "")
    instructions = str(_safe_attr(definition, "instructions", "") or "")
    tools = _safe_attr(definition, "tools", []) or []
    tool_choice = str(_safe_attr(definition, "tool_choice", "auto") or "auto")

    if not model:
        raise ValueError(
            f"Agent '{agent_name}' has no model set in its definition. "
            "Run foundry-agent-setup.py to provision the agent."
        )

    return AgentDefinition(model=model, instructions=instructions, tools=list(tools), tool_choice=tool_choice)


def create_openai_client() -> OpenAI:
    if get_bearer_token_provider is None:
        raise RuntimeError(
            "Install azure-identity to invoke Azure AI Foundry agents, then authenticate with "
            "DefaultAzureCredential (for example via az login or azure/login in GitHub Actions)."
        )

    credential = _get_credential()
    token_provider = get_bearer_token_provider(credential, "https://ai.azure.com/.default")
    base_url = _build_direct_base_url(PROJECT_ENDPOINT)

    if PROJECT_API_KEY:
        print(
            "⚠️  AZURE_AI_PROJECT_API_KEY is set, but Azure AI Foundry agent invocation uses Entra "
            "authentication. Falling back to DefaultAzureCredential."
        )

    return OpenAI(
        api_key=token_provider,
        base_url=base_url,
    )


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


def is_agent_not_found_error(exc: Exception) -> bool:
    status_code = _safe_get(exc, "status_code")
    if status_code is None:
        response = _safe_get(exc, "response")
        status_code = _safe_get(response, "status_code")

    message = str(exc).lower()
    return bool(
        status_code == 404
        or ("not_found" in message and "404" in message)
        or ("resourcenotfounderror" in type(exc).__name__.lower())
        or ("agent" in message and "not found" in message)
    )


def check_agent_available() -> None:
    """Confirm the agent definition exists in Azure AI Foundry."""
    get_agent_definition(AGENT_NAME)


def _downgrade_openapi_spec_in_tools(tools: list[Any]) -> list[Any]:
    """Recursively downgrade any embedded OpenAPI 3.1.0 spec to 3.0.0.

    Azure AI Foundry Agents API rejects 3.1.0 specs.  The stored agent definition
    may still contain a 3.1.0 spec if it was registered before the API was patched.
    This converts:
      anyOf: [{type: X}, {type: null}]  →  {type: X, nullable: true}
    and sets "openapi" version to "3.0.0".
    """
    import copy

    def _patch_schema(obj: Any) -> Any:
        if isinstance(obj, list):
            return [_patch_schema(item) for item in obj]
        if not isinstance(obj, dict):
            return obj

        result = {}
        for k, v in obj.items():
            if k == "openapi" and v in ("3.1.0", "3.1"):
                result[k] = "3.0.0"
            elif k == "responses" and isinstance(v, dict):
                # Drop 422 FastAPI validation-error responses
                result[k] = {code: _patch_schema(r) for code, r in v.items() if code != "422"}
            elif k == "schemas" and isinstance(v, dict):
                # Drop FastAPI validation-error component schemas
                result[k] = {
                    name: _patch_schema(s)
                    for name, s in v.items()
                    if name not in ("HTTPValidationError", "ValidationError")
                }
            elif k == "anyOf" and isinstance(v, list):
                null_items = [s for s in v if isinstance(s, dict) and s.get("type") == "null"]
                non_null = [s for s in v if not (isinstance(s, dict) and s.get("type") == "null")]
                if non_null:
                    # Use first non-null type; add nullable if null entries were present
                    merged = dict(_patch_schema(non_null[0]))
                    if null_items:
                        merged["nullable"] = True
                    for sk, sv in obj.items():
                        if sk != "anyOf":
                            merged.setdefault(sk, sv)
                    return merged
                else:
                    result["nullable"] = True
                    for sk, sv in obj.items():
                        if sk != "anyOf":
                            result[sk] = _patch_schema(sv)
                    return result
            else:
                result[k] = _patch_schema(v)
        return result

    return [_patch_schema(copy.deepcopy(tool)) for tool in tools]


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
    agent_def = get_agent_definition(AGENT_NAME)
    client = create_openai_client()

    create_kwargs: dict[str, Any] = {
        "model": agent_def.model,
        "input": prompt,
    }
    if agent_def.instructions:
        create_kwargs["instructions"] = agent_def.instructions
    if agent_def.tools:
        create_kwargs["tools"] = _downgrade_openapi_spec_in_tools(agent_def.tools)  # type: ignore[assignment]
        create_kwargs["tool_choice"] = resolve_tool_choice(prompt, agent_def.tool_choice)

    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            if stream:
                with client.responses.stream(**create_kwargs) as stream_ctx:
                    for event in stream_ctx:
                        event_type = str(_safe_get(event, "type", ""))
                        if event_type == "response.output_text.delta":
                            delta = _safe_get(event, "delta", "")
                            if delta:
                                print(delta, end="", flush=True)
                    print()
                    final_response = stream_ctx.get_final_response()
            else:
                final_response = client.responses.create(**create_kwargs)

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


def _is_tool_user_error(exc: Exception) -> bool:
    """Return True when the API call failed because a tool returned an error response."""
    message = str(exc)
    return "tool_user_error" in message or "tool_user_error" in str(getattr(exc, "code", ""))


def run_tests(
    stream: bool = False,
    test_prompts: list[str] | None = None,
    expected_invalid_tickers: list[str] | None = None,
) -> None:
    cases = resolve_test_prompts(test_prompts)
    invalid_tickers = resolve_expected_invalid_tickers(expected_invalid_tickers)

    print("Running end-to-end test cases...\n")
    results: list[dict[str, Any]] = []
    failures: list[str] = []

    for prompt in cases:
        is_expected_invalid_ticker_case = prompt_mentions_ticker(prompt, invalid_tickers)
        try:
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

            if not is_expected_invalid_ticker_case:
                if not result.tool_calls:
                    failures.append(f"Prompt '{prompt}' returned no tool calls.")
                elif not order_ok:
                    failures.append(
                        f"Prompt '{prompt}' did not call tools in expected order: {result.tool_calls}"
                    )
        except RuntimeError as exc:
            # For the invalid-ticker test case, a tool_user_error means the agent correctly
            # attempted to call the API and the API rejected the invalid ticker — acceptable.
            if is_expected_invalid_ticker_case and _is_tool_user_error(exc):
                print(f"Prompt: {prompt}")
                print("Tool call attempted and returned expected API error (tool_user_error for invalid ticker). ✅")
                results.append(
                    {
                        "prompt": prompt,
                        "tool_calls": [],
                        "tool_order_ok": False,
                        "output_excerpt": f"tool_user_error (expected for invalid ticker): {str(exc)[:200]}",
                    }
                )
                print()
            else:
                raise

    print("JSON summary:")
    print(json.dumps(results, indent=2))

    if failures:
        print("\nTest failures:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stock-forecast-agent via Azure AI Foundry responses.create")
    parser.add_argument("prompt", nargs="?", help="Prompt sent to the agent. If this is a bare ticker symbol, a stock-analysis prompt is generated.")
    parser.add_argument("--ticker", help="Ticker symbol to analyze. This builds the prompt automatically.")
    parser.add_argument("--days", type=int, default=DEFAULT_ANALYSIS_DAYS, help="Number of days of context to request when building a prompt from a ticker.")
    parser.add_argument("--context", default="", help="Additional guidance appended when building a prompt from a ticker.")
    parser.add_argument("--tests", action="store_true", help="Run required end-to-end test cases")
    parser.add_argument("--test-prompt", action="append", dest="test_prompts", help="Prompt to include in --tests mode. Repeat to supply multiple prompts.")
    parser.add_argument(
        "--expected-invalid-ticker",
        action="append",
        dest="expected_invalid_tickers",
        help="Ticker symbol allowed to raise tool_user_error in --tests mode. Repeat to supply multiple symbols.",
    )
    parser.add_argument("--stream", action="store_true", help="Enable streaming response output when available")
    parser.add_argument(
        "--check-agent",
        action="store_true",
        help="Validate that the configured Azure AI Foundry agent exists and is reachable",
    )
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be greater than 0")
    return args


def main() -> None:
    args = parse_args()

    if args.check_agent:
        try:
            check_agent_available()
        except Exception as exc:
            if is_agent_not_found_error(exc):
                print(
                    f"::warning::Azure AI Foundry agent '{AGENT_NAME}' was not found at "
                    f"{PROJECT_ENDPOINT}. Run infra/azure/foundry-agent-setup.py or set refresh_agent=true."
                )
                raise SystemExit(10) from exc
            raise

        print(f"✅ Azure AI Foundry agent '{AGENT_NAME}' is reachable.")
        return

    if args.tests:
        run_tests(
            stream=args.stream,
            test_prompts=args.test_prompts,
            expected_invalid_tickers=args.expected_invalid_tickers,
        )
        return

    resolved_prompt = resolve_prompt(
        prompt=args.prompt,
        ticker=args.ticker,
        days=args.days,
        context=args.context,
    )
    result = run_agent_prompt(prompt=resolved_prompt, stream=args.stream)

    sep = "─" * 68
    print(f"\n{sep}")
    print(f"Prompt : {result.prompt}")
    print(f"{sep}")
    if result.tool_calls:
        print(f"OpenAPI tool calls made by agent ({len(result.tool_calls)}):")
        for i, tc in enumerate(result.tool_calls, 1):
            print(f"  {i}. {tc}")
        order_ok = verify_tool_order(result.tool_calls)
        print(f"Tool order (price_history → technicals → news_sentiment): {'✅ OK' if order_ok else '⚠️  unexpected order'}")
    else:
        print("⚠️  No tool calls recorded in response — agent may have answered from training data.")
    print(f"{sep}")
    print("\nAgent response:\n")
    print(result.output_text)


if __name__ == "__main__":
    main()