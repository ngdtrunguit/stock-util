"""Azure AI Foundry — stock-forecast-agent client.

Calls the stock-forecast-agent using the Azure AI Foundry Responses API
via the azure-ai-projects SDK.

Usage:
    pip install azure-ai-projects azure-identity openai
    az login
    python services/foundry_client.py
    python services/foundry_client.py "Analyze NVDA with 90-day context"
"""

from __future__ import annotations

import os
import sys
from urllib.parse import quote

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────

ENDPOINT = os.environ.get(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper",
)
AGENT_NAME = os.environ.get("AZURE_AI_AGENT_NAME", "stock-forecast-agent")

AGENT_API_VERSION = os.environ.get("AZURE_AI_AGENT_API_VERSION", "2025-11-15-preview")

# ── Client ────────────────────────────────────────────────────────────────────


def _build_openai_client():
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(credential, "https://ai.azure.com/.default")
    base_url = (
        f"{ENDPOINT.rstrip('/')}"
        f"/applications/{quote(AGENT_NAME, safe='')}/protocols/openai"
    )
    return OpenAI(
        api_key=token_provider,
        base_url=base_url,
        default_query={"api-version": AGENT_API_VERSION},
    )


# ── Run ───────────────────────────────────────────────────────────────────────


def main() -> None:
    user_message = sys.argv[1] if len(sys.argv) > 1 else "Analyze TSLA with 180-day context."
    print(f"Asking agent '{AGENT_NAME}': {user_message}\n")

    client = _build_openai_client()

    response = client.responses.create(input=user_message)

    # Print the assistant output
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        print(output_text)
        return

    # Fallback: iterate output items for message content
    output_items = getattr(response, "output", []) or []
    for item in output_items:
        if getattr(item, "type", None) == "message":
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", None) == "output_text":
                    text = getattr(block, "text", "")
                    if text:
                        print(text)


if __name__ == "__main__":
    main()
