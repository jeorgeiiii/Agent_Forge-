# AgentForge API

FastAPI service for graph validation, storage, and Temporal workflow submission.

## Local development

From the repository root, start the backing services:

```powershell
docker compose -f infra/docker-compose.yml up --build
```

The API is available at `http://localhost:8000` and its OpenAPI page at
`http://localhost:8000/docs`. The Next.js app is at `http://localhost:3000`;
Temporal's development UI is at `http://localhost:8233`.

The root `.env` contains non-secret local defaults. Copilot suggestions remain
disabled until `COPILOT_API_URL` and `COPILOT_API_KEY` point to an
OpenAI-compatible provider.

## Graph definition

A graph has one `start` and one `end` node. Intermediate nodes can be `template`
or `http`; edges must form a directed acyclic graph reachable from the start.
Template values use Jinja expressions such as `{{ input.name }}` or
`{{ nodes.previous_node_id }}`. HTTP nodes accept only public destinations on
ports 80 and 443.