"""Azure AI Foundry — stock-forecast-agent client.

Usage:
    pip install azure-ai-agents azure-identity
    az login
    python services/foundry_client.py
    python services/foundry_client.py "Analyze NVDA with 90-day context"
"""

from __future__ import annotations

import os
import sys

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import MessageTextContent
from azure.identity import DefaultAzureCredential

# ── Config ────────────────────────────────────────────────────────────────────

# Project endpoint required by this SDK build.
ENDPOINT = "https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper"
AGENT_NAME = "getstocklist"
AGENT_MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_AGENT_MODEL_DEPLOYMENT", "")

# Optional override when your agent has a different display name.
AGENT_NAME = os.environ.get("AZURE_AI_AGENT_NAME", AGENT_NAME)

# ── Client ────────────────────────────────────────────────────────────────────

agents_client = AgentsClient(
    endpoint=ENDPOINT,
    credential=DefaultAzureCredential(),
)

# ── Run ───────────────────────────────────────────────────────────────────────

def main() -> None:
    user_message = sys.argv[1] if len(sys.argv) > 1 else "Analyze TSLA with 180-day context."
    print(f"Asking agent '{AGENT_NAME}': {user_message}\n")

    with agents_client:
        # List all agents — debug name mismatches
        all_agents = list(agents_client.list_agents())
        print(f"[debug] agents found: {[a.name for a in all_agents]}")

        agent = next((a for a in all_agents if a.name == AGENT_NAME), None)
        if agent is None:
            if not AGENT_MODEL_DEPLOYMENT:
                raise RuntimeError(
                    f"Agent '{AGENT_NAME}' not found. "
                    f"Available: {[a.name for a in all_agents]}. "
                    "Set AZURE_AI_AGENT_MODEL_DEPLOYMENT (for example: gpt-4o) "
                    "to auto-create it in this project."
                )

            print(
                f"[debug] creating agent '{AGENT_NAME}' with model deployment "
                f"'{AGENT_MODEL_DEPLOYMENT}'"
            )
            agent = agents_client.create_agent(
                model=AGENT_MODEL_DEPLOYMENT,
                name=AGENT_NAME,
                instructions=(
                    "You are a stock analysis assistant. Use available tools to fetch "
                    "price history, compute technical indicators, and summarize results."
                ),
            )

        # Create thread, post message, run to completion
        thread = agents_client.create_thread()
        agents_client.create_message(
            thread_id=thread.id,
            role="user",
            content=user_message,
        )

        run = agents_client.create_and_process_run(
            thread_id=thread.id,
            agent_id=agent.id,
        )

        if run.status == "failed":
            raise RuntimeError(f"Run failed: {run.last_error}")

        # Print assistant messages
        messages = agents_client.list_messages(thread_id=thread.id)
        for msg in reversed(list(messages)):
            if msg.role == "assistant":
                for block in msg.content:
                    if isinstance(block, MessageTextContent):
                        print(block.text.value)


if __name__ == "__main__":
    main()
