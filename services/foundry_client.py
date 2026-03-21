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

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# ── Config ────────────────────────────────────────────────────────────────────

ENDPOINT = os.environ.get(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper",
)
AGENT_NAME = os.environ.get("AZURE_AI_AGENT_NAME", "stock-forecast-agent")

# Optional API key override (uses DefaultAzureCredential when not set).
PROJECT_API_KEY = os.environ.get("AZURE_AI_PROJECT_API_KEY", "")

# ── Client ────────────────────────────────────────────────────────────────────


def _build_openai_client():
    if PROJECT_API_KEY:
        from openai import OpenAI

        return OpenAI(
            api_key=PROJECT_API_KEY,
            base_url=f"{ENDPOINT.rstrip('/')}/openai/v1",
        )

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=ENDPOINT, credential=credential)
    return project_client.get_openai_client()


# ── Run ───────────────────────────────────────────────────────────────────────


def main() -> None:
    user_message = sys.argv[1] if len(sys.argv) > 1 else "Analyze TSLA with 180-day context."
    print(f"Asking agent '{AGENT_NAME}': {user_message}\n")

    client = _build_openai_client()

    response = client.responses.create(
        model=AGENT_NAME,
        input=user_message,
    )

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
