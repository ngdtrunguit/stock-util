"""Thin wrapper around run_agent.py for ad hoc Foundry calls.

Usage:
    pip install azure-ai-projects azure-identity openai
    az login
    python services/foundry_client.py
    python services/foundry_client.py NVDA
    python services/foundry_client.py "Analyze NVDA with 90-day context"
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import run_agent


# ── Run ───────────────────────────────────────────────────────────────────────


def main() -> None:
    user_message = sys.argv[1] if len(sys.argv) > 1 else run_agent.DEFAULT_ANALYSIS_TICKER
    prompt = run_agent.resolve_prompt(user_message)

    print(f"Asking agent '{run_agent.AGENT_NAME}': {prompt}\n")

    result = run_agent.run_agent_prompt(prompt=prompt)
    print(result.output_text)


if __name__ == "__main__":
    main()
