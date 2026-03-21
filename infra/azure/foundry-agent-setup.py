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
    Connection,
    ModelDeployment,
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
OPENAPI_CONNECTION_NAME = os.getenv("AZURE_AI_OPENAPI_CONNECTION_NAME", "")
OPENAPI_API_KEY_HEADER_NAME = os.getenv("AZURE_AI_OPENAPI_API_KEY_HEADER_NAME", "x-api-key")
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
FOUNDRY_HUB_RESOURCE_GROUP = os.getenv("AZURE_AI_FOUNDRY_HUB_RESOURCE_GROUP", "")
AUTO_CONNECTION_NAME = os.getenv("AZURE_AI_FOUNDRY_AUTO_CONNECTION_NAME", "stock-tools-api-key")

REQUIRED_POST_ENDPOINTS = (
    "/price_history",
    "/technicals",
    "/news_sentiment",
)

PREFERRED_MODEL_NAMES = (
    "gpt-5.1-chat",
    "gpt-5-chat",
    "gpt-4.1",
    "gpt-4o",
    "gpt-4.1-mini",
)


def _safe_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_openapi_target(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path or "/"
    if path.endswith('/openapi.json'):
        path = path[: -len('/openapi.json')] or '/'
    return urlunparse((parsed.scheme, parsed.netloc, path.rstrip('/') or '/', '', '', ''))


def resolve_model_deployment(project_client: AIProjectClient, requested_model: str) -> str:
    requested_model = requested_model.strip()
    if requested_model:
        return requested_model

    deployments = list(project_client.deployments.list())
    if not deployments:
        raise SystemExit(
            "No Azure AI Foundry model deployments were found. Set AZURE_AI_AGENT_MODEL_DEPLOYMENT explicitly."
        )

    def rank(deployment: ModelDeployment) -> tuple[int, int, str]:
        name = str(_safe_attr(deployment, 'name', '') or '')
        model_name = str(_safe_attr(deployment, 'model_name', _safe_attr(deployment, 'modelName', '')) or '')
        candidates = {name.lower(), model_name.lower()}
        for idx, preferred in enumerate(PREFERRED_MODEL_NAMES):
            pref = preferred.lower()
            if pref in candidates:
                return (0, idx, name)
        for idx, preferred in enumerate(PREFERRED_MODEL_NAMES):
            pref = preferred.lower()
            if any(pref in candidate for candidate in candidates if candidate):
                return (1, idx, name)
        return (2, 999, name)

    selected = sorted(deployments, key=rank)[0]
    resolved_name = str(_safe_attr(selected, 'name', '') or '')
    resolved_model_name = str(_safe_attr(selected, 'model_name', _safe_attr(selected, 'modelName', '')) or '')
    if not resolved_name:
        raise SystemExit(
            "Could not resolve a valid Azure AI Foundry deployment name. Set AZURE_AI_AGENT_MODEL_DEPLOYMENT explicitly."
        )

    print(
        "Resolved AZURE_AI_AGENT_MODEL_DEPLOYMENT automatically "
        f"to '{resolved_name}' (model: {resolved_model_name or 'unknown'})."
    )
    return resolved_name


def resolve_openapi_connection_id(project_client: AIProjectClient, explicit_id: str, explicit_name: str, openapi_spec_url: str) -> str:
    explicit_id = explicit_id.strip()
    if explicit_id:
        return explicit_id

    try:
        connections = list(project_client.connections.list(connection_type='CustomKeys'))
    except HttpResponseError as exc:
        if "PermissionDenied" in str(type(exc).__name__) or getattr(exc, 'status_code', None) in (403, 401):
            print(
                f"⚠️  Cannot list Foundry connections (permission denied). "
                "Falling back to auto-provisioning. Grant 'Azure AI Developer' role to the principal if this keeps failing."
            )
            return ''
        raise
    if not connections:
        return ''

    explicit_name = explicit_name.strip()
    if explicit_name:
        for connection in connections:
            if str(_safe_attr(connection, 'name', '') or '').strip() == explicit_name:
                resolved_id = str(_safe_attr(connection, 'id', '') or '')
                print(
                    "Resolved AZURE_AI_OPENAPI_CONNECTION_ID from AZURE_AI_OPENAPI_CONNECTION_NAME "
                    f"'{explicit_name}'."
                )
                return resolved_id
        raise SystemExit(
            f"Could not find Foundry connection named '{explicit_name}'. Set AZURE_AI_OPENAPI_CONNECTION_ID explicitly."
        )

    normalized_openapi_target = _normalize_openapi_target(openapi_spec_url)
    matching_connections: list[Connection] = []
    for connection in connections:
        target = str(_safe_attr(connection, 'target', '') or '').strip()
        if not target:
            continue
        normalized_target = _normalize_openapi_target(target)
        if normalized_target == normalized_openapi_target:
            matching_connections.append(connection)

    if len(matching_connections) == 1:
        resolved = str(_safe_attr(matching_connections[0], 'id', '') or '')
        print(
            "Resolved AZURE_AI_OPENAPI_CONNECTION_ID automatically from the matching CustomKeys connection "
            f"'{_safe_attr(matching_connections[0], 'name', '')}'."
        )
        return resolved

    default_matches = [c for c in matching_connections if bool(_safe_attr(c, 'is_default', False))]
    if len(default_matches) == 1:
        resolved = str(_safe_attr(default_matches[0], 'id', '') or '')
        print(
            "Resolved AZURE_AI_OPENAPI_CONNECTION_ID automatically from the default matching CustomKeys connection "
            f"'{_safe_attr(default_matches[0], 'name', '')}'."
        )
        return resolved

    if len(connections) == 1:
        resolved = str(_safe_attr(connections[0], 'id', '') or '')
        print(
            "Resolved AZURE_AI_OPENAPI_CONNECTION_ID automatically from the only CustomKeys connection in the project "
            f"'{_safe_attr(connections[0], 'name', '')}'."
        )
        return resolved

    return ''


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


def _hub_name_from_endpoint(endpoint: str) -> tuple[str, str]:
    """Return (account_name, project_name) from a Foundry project endpoint URL.

    E.g. https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper
         → ('stock-helper-resource', 'stock-helper')
    """
    parsed = urlparse(endpoint)
    hostname = parsed.hostname or ""
    account_name = hostname.split(".")[0] if hostname else ""
    path_parts = [p for p in parsed.path.split("/") if p]
    project_name = ""
    for i, part in enumerate(path_parts):
        if part == "projects" and i + 1 < len(path_parts):
            project_name = path_parts[i + 1]
            break
    return account_name, project_name


def _get_arm_access_token(credential: DefaultAzureCredential) -> str:
    return credential.get_token("https://management.azure.com/.default").token


def _find_hub_resource_group(account_name: str, subscription_id: str, token: str) -> str:
    """Discover the resource group of a CognitiveServices account by name."""
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}/resources"
        f"?api-version=2021-04-01"
        f"&$filter=resourceType eq 'Microsoft.CognitiveServices/accounts' and name eq '{account_name}'"
        f"&$top=1"
    )
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        resp.raise_for_status()
        resources = resp.json().get("value", [])
        if not resources:
            return ""
        resource_id = resources[0].get("id", "")
        parts = resource_id.split("/")
        for i, part in enumerate(parts):
            if part.lower() == "resourcegroups" and i + 1 < len(parts):
                return parts[i + 1]
        return ""
    except Exception as exc:
        print(f"⚠️  Could not discover resource group for account '{account_name}': {exc}")
        return ""


def provision_openapi_connection(
    credential: DefaultAzureCredential,
    api_key: str,
    header_name: str,
    base_url: str,
) -> str:
    """Create or update a Foundry CustomKeys connection for the Stock Tools API.

    Uses the CognitiveServices ARM API:
      PUT .../Microsoft.CognitiveServices/accounts/{account}/projects/{project}/connections/{name}

    Returns the connection name on success, or an empty string when it cannot proceed.
    """
    if not AZURE_SUBSCRIPTION_ID:
        print(
            "⚠️  AZURE_SUBSCRIPTION_ID is not set — cannot auto-create the Foundry connection. "
            "Set AZURE_AI_OPENAPI_CONNECTION_ID explicitly, or set AZURE_SUBSCRIPTION_ID to enable auto-provisioning."
        )
        return ""

    account_name, project_name = _hub_name_from_endpoint(PROJECT_ENDPOINT)
    if not account_name or not project_name:
        print("⚠️  Cannot determine Foundry account/project names from PROJECT_ENDPOINT.")
        return ""

    resource_group = FOUNDRY_HUB_RESOURCE_GROUP
    if not resource_group:
        print(f"Discovering resource group for Foundry account '{account_name}' via Azure management API...")
        token = _get_arm_access_token(credential)
        resource_group = _find_hub_resource_group(account_name, AZURE_SUBSCRIPTION_ID, token)
        if not resource_group:
            print(
                f"⚠️  Could not discover resource group for account '{account_name}'. "
                "Set AZURE_AI_FOUNDRY_HUB_RESOURCE_GROUP to skip discovery."
            )
            return ""

    connection_name = AUTO_CONNECTION_NAME
    put_url = (
        f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/projects/{project_name}"
        f"/connections/{connection_name}?api-version=2025-04-01-preview"
    )
    body = {
        "properties": {
            "category": "CustomKeys",
            "target": base_url,
            "authType": "CustomKeys",
            "credentials": {
                "keys": {
                    header_name: api_key
                }
            },
        }
    }

    try:
        token = _get_arm_access_token(credential)
        resp = requests.put(
            put_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if resp.status_code == 404:
            # CognitiveServices project connections may not support this ARM path; fall back to
            # reporting the connection name so the caller retries via data-plane list.
            print(
                f"⚠️  ARM path for project connections returned 404. "
                "The connection may need to be created manually in Azure AI Foundry portal. "
                f"Connection name to create: '{connection_name}' (type: CustomKeys, target: {base_url})."
            )
            return ""
        resp.raise_for_status()
        print(
            f"Provisioned Foundry CustomKeys connection '{connection_name}' "
            f"(account: {account_name}, project: {project_name}, rg: {resource_group}, header: {header_name})."
        )
        return connection_name
    except Exception as exc:
        print(f"⚠️  Failed to provision Foundry connection '{connection_name}': {exc}")
        return ""


def build_openapi_tool(spec: dict[str, Any], connection_id: str) -> OpenApiTool:
    spec = normalize_openapi_for_foundry(spec)

    if connection_id:
        spec = _inject_api_key_security(spec, OPENAPI_API_KEY_HEADER_NAME)
        auth_details = OpenApiProjectConnectionAuthDetails(
            security_scheme=OpenApiProjectConnectionSecurityScheme(
                project_connection_id=connection_id
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
    parser.add_argument("--model", default=AGENT_MODEL_DEPLOYMENT, help="Model deployment name (for example, gpt-4.1). Auto-resolved when omitted.")
    parser.add_argument("--connection-id", default=OPENAPI_CONNECTION_ID, help="Optional Foundry CustomKeys connection ID for the OpenAPI tool. Auto-resolved when omitted.")
    parser.add_argument("--connection-name", default=OPENAPI_CONNECTION_NAME, help="Optional Foundry CustomKeys connection name to resolve before falling back to auto-detection.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

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

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=not _allow_interactive_browser())

    try:
        with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential) as project_client:
            model_deployment = resolve_model_deployment(project_client, args.model)
            connection_id = resolve_openapi_connection_id(
                project_client,
                explicit_id=args.connection_id,
                explicit_name=args.connection_name,
                openapi_spec_url=OPENAPI_SPEC_URL,
            )

            if connection_id:
                print(
                    "Using project connection auth for OpenAPI tool "
                    f"(connection id: {connection_id}, header: {OPENAPI_API_KEY_HEADER_NAME})."
                )
            else:
                api_key = os.getenv("STOCK_TOOLS_API_KEY", "").strip()
                if api_key:
                    # Auto-provision a CustomKeys connection so Foundry injects the API key at call time.
                    provisioned_name = provision_openapi_connection(
                        credential=credential,
                        api_key=api_key,
                        header_name=OPENAPI_API_KEY_HEADER_NAME,
                        base_url=_normalize_openapi_target(OPENAPI_SPEC_URL),
                    )
                    if provisioned_name:
                        # Re-query via Foundry SDK to get the canonical connection ID.
                        try:
                            conn = project_client.connections.get(provisioned_name)
                            connection_id = str(_safe_attr(conn, 'id', '') or '') or provisioned_name
                        except Exception:
                            connection_id = provisioned_name
                        print(
                            f"Using auto-provisioned project connection auth for OpenAPI tool "
                            f"(connection: {connection_id}, header: {OPENAPI_API_KEY_HEADER_NAME})."
                        )
                    else:
                        raise SystemExit(
                            "STOCK_TOOLS_API_KEY is set but Foundry connection auto-provisioning failed. "
                            "Provide AZURE_SUBSCRIPTION_ID (and optionally AZURE_AI_FOUNDRY_HUB_RESOURCE_GROUP) "
                            "for auto-provisioning, or set AZURE_AI_OPENAPI_CONNECTION_ID explicitly."
                        )
                else:
                    print("Using anonymous auth for OpenAPI tool.")

            tool = build_openapi_tool(spec, connection_id=connection_id)
            upsert_agent(project_client, model_deployment, tool)
    except HttpResponseError as exc:
        detail = str(exc)
        if "deployments/read" in detail or ("PermissionDenied" in detail and "deployments" in detail):
            raise SystemExit(
                "Azure API error while provisioning agent: missing Foundry deployment read permission. "
                "Grant the workflow principal the required Foundry data-plane access or skip agent refresh in CI. "
                f"Original error: {exc}"
            ) from exc
        if "PermissionDenied" in detail or exc.status_code in (401, 403):
            raise SystemExit(
                f"Azure AI Foundry permission denied (HTTP {exc.status_code}). "
                f"The CI service principal needs the 'Azure AI Developer' role on the Foundry resource. "
                f"Run:\n"
                f"  az role assignment create \\\n"
                f"    --assignee <AZURE_CLIENT_ID> \\\n"
                f"    --role 'Azure AI Developer' \\\n"
                f"    --scope /subscriptions/<SUBSCRIPTION>/resourceGroups/<RG>/providers/Microsoft.CognitiveServices/accounts/stock-helper-resource\n"
                f"Original error: {exc}"
            ) from exc
        raise SystemExit(f"Azure API error while provisioning agent: {exc}") from exc

    print("OpenAPI tool + agent provisioning complete.")


if __name__ == "__main__":
    main()
