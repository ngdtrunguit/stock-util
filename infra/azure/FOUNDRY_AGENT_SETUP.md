# Azure AI Foundry OpenAPI Agent Setup

## 1. Environment variables

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper"
export AZURE_AI_AGENT_NAME="stock-forecast-agent"
export AZURE_AI_AGENT_MODEL_DEPLOYMENT="gpt-4.1"
export STOCK_TOOLS_OPENAPI_URL="https://stock-tools-api-dev-app.calmstone-a9644956.eastus.azurecontainerapps.io/openapi.json"
export AZURE_AI_OPENAPI_CONNECTION_ID="<optional-project-connection-id>"  # optional if auto-discovery can find a matching CustomKeys connection
export AZURE_AI_OPENAPI_CONNECTION_NAME="<optional-project-connection-name>"
export AZURE_AI_OPENAPI_API_KEY_HEADER_NAME="x-api-key"
```

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

## 3. Create or update the agent and OpenAPI tool

This uses `azure-ai-projects` + `DefaultAzureCredential` (Entra auth) for provisioning:

```bash
az login
python infra/azure/foundry-agent-setup.py --model "$AZURE_AI_AGENT_MODEL_DEPLOYMENT"
# or let the script auto-resolve the deployment + matching CustomKeys connection
python infra/azure/foundry-agent-setup.py
```

What the setup script does:
- Downloads and validates the OpenAPI spec from `STOCK_TOOLS_OPENAPI_URL`
- Verifies required POST routes exist: `/price_history`, `/technicals`, `/news_sentiment`
- Enforces OpenAPI tool constraints from Microsoft docs (`operationId`, OpenAPI 3.x)
- Registers an OpenAPI tool:
  - Anonymous auth when no connection ID can be resolved
  - Project connection auth when `AZURE_AI_OPENAPI_CONNECTION_ID` is set or auto-resolved
- Creates `stock-forecast-agent` if missing, or publishes a new version if it exists
- Auto-resolves a model deployment when `AZURE_AI_AGENT_MODEL_DEPLOYMENT` is omitted
- Auto-resolves a matching CustomKeys connection by ID, connection name, URL match, or single-connection fallback

GitHub Actions note:
- The `test-azure-ai-agent` workflow normally runs against the already-provisioned agent.
- Set `refresh_agent=true` only when you intentionally want CI to republish the agent/tool definition.
- `agent_model_deployment` is optional in the workflow; when omitted during refresh, `foundry-agent-setup.py` auto-resolves a deployment from the Foundry project after Azure login.

## 4. Run one prompt via responses.create

```bash
python run_agent.py "Analyze AAPL stock"
```

`run_agent.py` calls the published agent application endpoint and uses Entra auth (`az login` locally or `azure/login` in GitHub Actions), not the project API key.

## 5. Run required end-to-end validation prompts

```bash
python run_agent.py --tests
```

Test prompts:
- Analyze TSLA
- Analyze NVDA short term
- Analyze BADTICKERZZZZ (tool-failure simulation path)

## 6. MCP config

MCP server configuration is stored in:
- `.vscode/mcp.json`

Included servers:
- `microsoft-docs` (learn.microsoft.com)
- `openapi-fastapi` (OpenAPI/FastAPI schema fetch/validation workflows)
- `azure-cli` (optional)
- `terraform` (optional)

## Troubleshooting

- `Agent call failed`:
  - Confirm `AZURE_AI_PROJECT_ENDPOINT` is the Foundry project endpoint, not the Azure OpenAI endpoint.
  - Confirm you authenticated with Entra (`az login` locally or `azure/login` in CI) before running `run_agent.py`.
  - Confirm the published application endpoint for `AZURE_AI_AGENT_NAME` exists and is healthy.

- Tool calls fail with `401 Invalid or missing API key`:
  - Create a CustomKeys project connection in Foundry with API key header `x-api-key`.
  - Prefer setting `AZURE_AI_OPENAPI_CONNECTION_NAME` or `AZURE_AI_OPENAPI_CONNECTION_ID`, then re-run `foundry-agent-setup.py`.
  - The setup script can also auto-resolve a matching connection when the connection target matches `STOCK_TOOLS_OPENAPI_URL`.
  - Do not hardcode connection IDs across environments unless every environment reuses the same Foundry project, because connection IDs are environment-specific.

- `setup.py auth failure`:
  - Provisioning uses Entra auth. Run `az login` and ensure access to the Foundry project.

- `missing Foundry deployment read permission`:
  - The workflow principal can invoke the agent but still fail refresh if it lacks Foundry data-plane permissions such as `deployments/read`.
  - Grant the principal the required Foundry permissions, or run the workflow with `refresh_agent=false` to reuse the existing published agent.

- `missing model deployment`:
  - Set `AZURE_AI_AGENT_MODEL_DEPLOYMENT` to a deployed model name in your Foundry project when you want to force a specific deployment.
  - Otherwise let `foundry-agent-setup.py` auto-resolve the deployment from the Foundry project.
  - Do not assume `gpt-5.1-chat` exists in every Foundry project; deployment names are project-specific.

- Tool not called in expected order:
  - Re-run setup to ensure latest instructions were published.
  - Validate OpenAPI spec still has stable `operationId` values.
