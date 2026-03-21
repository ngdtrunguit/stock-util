# Azure AI Foundry OpenAPI Agent Setup

## 1. Environment variables

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://stock-helper-resource.services.ai.azure.com/api/projects/stock-helper"
export AZURE_AI_PROJECT_API_KEY="<project-api-key>"
export AZURE_AI_AGENT_NAME="stock-forecast-agent"
export AZURE_AI_AGENT_MODEL_DEPLOYMENT="gpt-4.1"
export STOCK_TOOLS_OPENAPI_URL="https://stock-tools-api-dev-app.calmstone-a9644956.eastus.azurecontainerapps.io/openapi.json"
export AZURE_AI_OPENAPI_CONNECTION_ID="<optional-project-connection-id>"
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
```

What the setup script does:
- Downloads and validates the OpenAPI spec from `STOCK_TOOLS_OPENAPI_URL`
- Verifies required POST routes exist: `/price_history`, `/technicals`, `/news_sentiment`
- Enforces OpenAPI tool constraints from Microsoft docs (`operationId`, OpenAPI 3.x)
- Registers an OpenAPI tool:
  - Anonymous auth when `AZURE_AI_OPENAPI_CONNECTION_ID` is not set
  - Project connection auth when `AZURE_AI_OPENAPI_CONNECTION_ID` is set
- Creates `stock-forecast-agent` if missing, or publishes a new version if it exists

## 4. Run one prompt via responses.create

```bash
python run_agent.py "Analyze AAPL stock"
```

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
  - Confirm `AZURE_AI_PROJECT_API_KEY` is valid and not expired.
  - Confirm `AZURE_AI_PROJECT_ENDPOINT` is project endpoint, not the Azure OpenAI endpoint.

- Tool calls fail with `401 Invalid or missing API key`:
  - Create a custom keys project connection in Foundry with API key header `x-api-key`.
  - Set `AZURE_AI_OPENAPI_CONNECTION_ID` to that connection ID.
  - Re-run `foundry-agent-setup.py` so the tool uses project-connection auth.

- `setup.py auth failure`:
  - Provisioning uses Entra auth. Run `az login` and ensure access to the Foundry project.

- `missing model deployment`:
  - Set `AZURE_AI_AGENT_MODEL_DEPLOYMENT` to a deployed model name in your Foundry project.

- Tool not called in expected order:
  - Re-run setup to ensure latest instructions were published.
  - Validate OpenAPI spec still has stable `operationId` values.
