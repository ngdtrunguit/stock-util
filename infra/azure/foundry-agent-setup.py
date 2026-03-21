"""Provision stock-forecast-agent with an OpenAPI tool in Azure AI Foundry.

This script follows the OpenAPI tool flow in Microsoft docs:
https://learn.microsoft.com/en-us/azure/foundry-classic/agents/how-to/tools-classic/openapi-spec

Usage:
    python infra/azure/foundry-agent-setup.py
    python infra/azure/foundry-agent-setup.py --model gpt-4.1
"""

from __future__ import annotations

import argparse
import copy
import os
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    OpenApiAnonymousAuthDetails,
    OpenApiFunctionDefinition,
    OpenApiProjectConnectionAuthDetails,
    OpenApiProjectConnectionSecurityScheme,
    OpenApiTool,
    PromptAgentDefinition,
)
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = os.getenv(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper",
)
OPENAPI_SPEC_URL = os.getenv(
    "STOCK_TOOLS_OPENAPI_URL",
    "https://stock-tools-api-dev-app.calmstone-a9644956.eastus.azurecontainerapps.io/openapi.json",
)
AGENT_NAME = os.getenv("AZURE_AI_AGENT_NAME", "stock-forecast-agent")
AGENT_MODEL_DEPLOYMENT = os.getenv("AZURE_AI_AGENT_MODEL_DEPLOYMENT", "")
OPENAPI_CONNECTION_ID = os.getenv("AZURE_AI_OPENAPI_CONNECTION_ID", "")
OPENAPI_API_KEY_HEADER_NAME = os.getenv("AZURE_AI_OPENAPI_API_KEY_HEADER_NAME", "x-api-key")

REQUIRED_POST_ENDPOINTS = (
    "/price_history",
    "/technicals",
    "/news_sentiment",
)

AGENT_INSTRUCTIONS = """You are stock-forecast-agent.

You have access to external tools hosted on Stock Tools API.
Always use the tools for factual market data and technical metrics before giving conclusions.

Rules:
1. Never fabricate prices, indicators, or headlines.
2. If a tool call fails, explain the failure briefly and return a fallback response with uncertainty.
3. Keep outputs concise and decision-oriented.
4. Use UTC date references when discussing recency.
5. Mention data freshness limits and that Yahoo Finance may have delays.
6. Do not reveal secrets or API keys in responses.

Workflow:
1. Call POST /price_history with:
   {
     "ticker": "<SYMBOL>",
     "days": 180
   }
2. From returned history, call POST /technicals with:
   {
     "history": <history from price_history>
   }
3. Call POST /news_sentiment with:
   {
     "ticker": "<SYMBOL>",
     "days": 30
   }
4. Synthesize:
   - Trend (up/down/sideways)
   - RSI interpretation (overbought/oversold/neutral)
   - Volatility context
   - News sentiment impact
   - Final bias: bullish, bearish, or neutral
   - Confidence score from 0 to 100 with one-line rationale

Output format:
- Ticker
- Market snapshot
- Technicals
- News sentiment
- Bias and confidence
- Key risks"""


def _allow_interactive_browser() -> bool:
    return os.getenv("AZURE_AI_ALLOW_INTERACTIVE_BROWSER", "").strip().lower() in {"1", "true", "yes", "on"}


def fetch_openapi_spec(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("OpenAPI spec must be a JSON object.")
    return payload


def validate_openapi_spec(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []

    version = str(spec.get("openapi", ""))
    if not version.startswith("3."):
        errors.append("OpenAPI version must be 3.x for OpenAPI tool support.")

    paths = spec.get("paths")
    if not isinstance(paths, dict):
        errors.append("OpenAPI spec is missing a valid 'paths' object.")
        return warnings, errors

    post_ops_found: list[str] = []
    for endpoint in REQUIRED_POST_ENDPOINTS:
        endpoint_item = paths.get(endpoint)
        if not isinstance(endpoint_item, dict) or "post" not in endpoint_item:
            errors.append(f"Missing POST endpoint in spec: {endpoint}")
            continue

        operation = endpoint_item["post"]
        post_ops_found.append(endpoint)
        operation_id = operation.get("operationId")
        if not isinstance(operation_id, str) or not operation_id.strip():
            errors.append(f"{endpoint} POST is missing operationId.")
        elif re.search(r"[^A-Za-z_-]", operation_id):
            errors.append(
                f"{endpoint} POST operationId '{operation_id}' contains unsupported characters. "
                "Use letters, '_' or '-'."
            )

        request_body = operation.get("requestBody", {})
        content = request_body.get("content", {}) if isinstance(request_body, dict) else {}
        if "application/json" not in content:
            warnings.append(f"{endpoint} POST does not declare application/json requestBody content.")

    if not post_ops_found:
        errors.append("No required POST operations were detected in the OpenAPI spec.")

    return warnings, errors


def _convert_nullable_anyof(node: Any) -> Any:
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and len(any_of) == 2:
            null_schema = next(
                (
                    item
                    for item in any_of
                    if isinstance(item, dict) and item.get("type") == "null"
                ),
                None,
            )
            value_schema = next(
                (
                    item
                    for item in any_of
                    if isinstance(item, dict) and item.get("type") != "null"
                ),
                None,
            )
            if null_schema is not None and value_schema is not None:
                merged = _convert_nullable_anyof(value_schema)
                if isinstance(merged, dict):
                    merged["nullable"] = True
                    return merged

        return {key: _convert_nullable_anyof(value) for key, value in node.items()}

    if isinstance(node, list):
        return [_convert_nullable_anyof(item) for item in node]

    return node


def normalize_openapi_for_foundry(spec: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(spec)
    normalized["openapi"] = "3.0.3"
    normalized.pop("jsonSchemaDialect", None)
    if not normalized.get("servers"):
        parsed = urlparse(OPENAPI_SPEC_URL)
        base_path = parsed.path
        if base_path.endswith("/openapi.json"):
            base_path = base_path[: -len("/openapi.json")]
        if not base_path:
            base_path = "/"
        base_url = urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", ""))
        normalized["servers"] = [{"url": base_url}]
    normalized = _convert_nullable_anyof(normalized)
    return normalized


def _remove_explicit_api_key_parameters(spec: dict[str, Any]) -> None:
    candidate_parameter_names = {"x-api-key", "x-api_key", "api_key", "apikey"}
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return

    for _, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for _, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            parameters = operation.get("parameters", [])
            if not isinstance(parameters, list):
                continue
            filtered: list[dict[str, Any]] = []
            for param in parameters:
                if not isinstance(param, dict):
                    continue
                name = str(param.get("name", "")).strip().lower()
                if name in candidate_parameter_names:
                    continue
                filtered.append(param)
            operation["parameters"] = filtered


def _inject_api_key_security(spec: dict[str, Any], header_name: str) -> dict[str, Any]:
    normalized_spec = dict(spec)
    components = dict(normalized_spec.get("components") or {})
    security_schemes = dict(components.get("securitySchemes") or {})
    security_schemes["apiKeyHeader"] = {
        "type": "apiKey",
        "name": header_name,
        "in": "header",
    }
    components["securitySchemes"] = security_schemes
    normalized_spec["components"] = components
    normalized_spec["security"] = [{"apiKeyHeader": []}]
    _remove_explicit_api_key_parameters(normalized_spec)
    return normalized_spec


def build_openapi_tool(spec: dict[str, Any]) -> OpenApiTool:
    spec = normalize_openapi_for_foundry(spec)

    if OPENAPI_CONNECTION_ID:
        spec = _inject_api_key_security(spec, OPENAPI_API_KEY_HEADER_NAME)
        auth_details = OpenApiProjectConnectionAuthDetails(
            security_scheme=OpenApiProjectConnectionSecurityScheme(
                project_connection_id=OPENAPI_CONNECTION_ID
            )
        )
    else:
        auth_details = OpenApiAnonymousAuthDetails()

    return OpenApiTool(
        openapi=OpenApiFunctionDefinition(
            name="stock_tools_api",
            description="Stock Tools API for price history, technical indicators, and news sentiment.",
            spec=spec,
            auth=auth_details,
        )
    )


def upsert_agent(project_client: AIProjectClient, model_deployment: str, tool: OpenApiTool) -> None:
    definition = PromptAgentDefinition(
        model=model_deployment,
        instructions=AGENT_INSTRUCTIONS,
        tools=[tool],
        tool_choice="auto",
    )

    try:
        result = project_client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=definition,
            description="Stock forecast agent with external OpenAPI tool",
        )
        print(f"Created/updated agent '{AGENT_NAME}' to version: {result.version}")
        return
    except ResourceNotFoundError:
        # Some API versions require creating the agent shell before creating versions.
        create_agent = getattr(project_client.agents, "_create_agent", None)
        if create_agent is None:
            raise

        created = create_agent(
            name=AGENT_NAME,
            definition=definition,
            description="Stock forecast agent with external OpenAPI tool",
        )
        print(f"Created agent shell '{AGENT_NAME}' (id: {created.id}).")
        result = project_client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=definition,
            description="Stock forecast agent with external OpenAPI tool",
        )
        print(f"Created first version for '{AGENT_NAME}': {result.version}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update stock-forecast-agent in Azure AI Foundry.")
    parser.add_argument("--model", default=AGENT_MODEL_DEPLOYMENT, help="Model deployment name (for example, gpt-4.1).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.model:
        raise SystemExit(
            "Missing model deployment name. Set AZURE_AI_AGENT_MODEL_DEPLOYMENT or pass --model."
        )

    spec = fetch_openapi_spec(OPENAPI_SPEC_URL)
    warnings, errors = validate_openapi_spec(spec)

    if warnings:
        print("OpenAPI validation warnings:")
        for item in warnings:
            print(f"- {item}")

    if errors:
        print("OpenAPI validation errors:")
        for item in errors:
            print(f"- {item}")
        raise SystemExit("OpenAPI validation failed; fix the spec before registration.")

    if OPENAPI_CONNECTION_ID:
        print(
            "Using project connection auth for OpenAPI tool "
            f"(connection id: {OPENAPI_CONNECTION_ID}, header: {OPENAPI_API_KEY_HEADER_NAME})."
        )
    else:
        print("Using anonymous auth for OpenAPI tool.")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=not _allow_interactive_browser())

    try:
        with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential) as project_client:
            tool = build_openapi_tool(spec)
            upsert_agent(project_client, args.model, tool)
    except HttpResponseError as exc:
        raise SystemExit(f"Azure API error while provisioning agent: {exc}") from exc

    print("OpenAPI tool + agent provisioning complete.")


if __name__ == "__main__":
    main()
