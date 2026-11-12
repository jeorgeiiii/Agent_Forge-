"""More integrations (API key / token, or no key) - grouped like the categories on the Integrations page.

Same rules as connector_catalog.py. GraphQL services (Linear, Monday) send a fixed `query` in const_body
and map fields into `variables`. Services that move money or touch personal-data enrichment are left
out on purpose (see the integrations policy notes).
"""

from .connectors import Connector, ConnectorAction as A, ConnectorField as F

_LIMIT = F("limit", "How many", "integer", help="Maximum number of results (leave empty for the service default).")
_RAW = ""  # auth_prefix for services that want the bare key in the header

# ------------------------------------------------------------------ Communication

DISCORD = Connector(
    id="discord", name="Discord", category="Communication",
    description="Send and read messages in Discord channels with your bot.",
    base_url="https://discord.com/api/v10", auth_prefix="Bot ", key_label="Bot token",
    key_help="discord.com/developers → your app → Bot → Reset Token. Invite the bot to your server "
             "with the Send Messages + Read Message History permissions.",
    docs_url="https://discord.com/developers/docs/resources/message",
    actions=(
        A("send_message", "Send message", "Post a message to a channel.", "POST", "/channels/{channel_id}/messages",
          fields=(F("channel_id", "Channel ID", required=True, help="Right-click the channel → Copy Channel ID."),
                  F("content", "Message", "text", required=True)),
          body={"content": "content"}, writes=True),
        A("read_messages", "Read recent messages", "Latest messages of a channel.", "GET",
          "/channels/{channel_id}/messages",
          fields=(F("channel_id", "Channel ID", required=True), _LIMIT), query={"limit": "limit"}),
    ),
)

# ------------------------------------------------------------------ Sales / CRM

HUBSPOT = Connector(
    id="hubspot", name="HubSpot", category="Sales",
    description="Create and list HubSpot contacts and deals.",
    base_url="https://api.hubapi.com", key_label="Private app token",
    key_help="HubSpot → Settings → Integrations → Private apps → Create. Scopes: crm.objects.contacts "
             "and crm.objects.deals (read/write). Copy the access token (pat-…).",
    docs_url="https://developers.hubspot.com/docs/api/crm/contacts",
    actions=(
        A("create_contact", "Create contact", "Add a contact.", "POST", "/crm/v3/objects/contacts",
          fields=(F("properties", "Properties", "object", required=True,
                    help='e.g. {"email": "ada@example.com", "firstname": "Ada"}'),),
          body={"properties": "properties"}, writes=True),
        A("list_contacts", "List contacts", "Recent contacts.", "GET", "/crm/v3/objects/contacts",
          fields=(_LIMIT,), query={"limit": "limit"}, result="results"),
        A("create_deal", "Create deal", "Add a deal to the pipeline.", "POST", "/crm/v3/objects/deals",
          fields=(F("properties", "Properties", "object", required=True,
                    help='e.g. {"dealname": "Acme renewal", "amount": "5000", "dealstage": "appointmentscheduled"}'),),
          body={"properties": "properties"}, writes=True),
    ),
)

# ------------------------------------------------------------------ Productivity

ASANA = Connector(
    id="asana", name="Asana", category="Productivity",
    description="Create tasks in Asana projects and list them.",
    base_url="https://app.asana.com/api/1.0", key_label="Personal access token",
    key_help="Asana → My settings → Apps → Developer apps → Create new token.",
    docs_url="https://developers.asana.com/reference/rest-api-reference",
    actions=(
        A("create_task", "Create task", "Add a task to a project.", "POST", "/tasks",
          fields=(F("projects", "Project IDs", "array", required=True, help='e.g. ["1203456789"] (from the project URL)'),
                  F("name", "Task name", required=True), F("notes", "Notes", "text"),
                  F("due_on", "Due date", help="YYYY-MM-DD")),
          body={"data.projects": "projects", "data.name": "name", "data.notes": "notes", "data.due_on": "due_on"},
          result="data", writes=True),
        A("list_tasks", "List project tasks", "Tasks of a project.", "GET", "/projects/{project_id}/tasks",
          fields=(F("project_id", "Project ID", required=True), _LIMIT),
          query={"limit": "limit"}, const_query={"opt_fields": "name,completed,due_on,assignee.name"}, result="data"),
    ),
)

LINEAR = Connector(
    id="linear", name="Linear", category="Productivity",
    description="Create Linear issues and look up teams.",
    base_url="https://api.linear.app", auth_prefix=_RAW, key_label="API key", errors_field="errors",
    key_help="Linear → Settings → Security & access → Personal API keys → New key.",
    docs_url="https://developers.linear.app/docs/graphql/working-with-the-graphql-api",
    actions=(
        A("create_issue", "Create issue", "Open an issue in a team.", "POST", "/graphql",
          fields=(F("team_id", "Team ID", required=True, help="Use 'List teams' to find it."),
                  F("title", "Title", required=True), F("description", "Description", "text")),
          const_body={"query": "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
                               "{ success issue { id identifier title url } } }"},
          body={"variables.input.teamId": "team_id", "variables.input.title": "title",
                "variables.input.description": "description"}, result="data", writes=True),
        A("list_teams", "List teams", "Teams and their IDs.", "POST", "/graphql",
          const_body={"query": "{ teams { nodes { id name key } } }"}, result="data"),
    ),
)

MONDAY = Connector(
    id="monday", name="Monday", category="Productivity",
    description="Add items to monday.com boards.",
    base_url="https://api.monday.com", auth_prefix=_RAW, key_label="API token", errors_field="errors",
    headers={"API-Version": "2024-10"},
    key_help="monday.com → your avatar → Developers → My access tokens → Show.",
    docs_url="https://developer.monday.com/api-reference/docs",
    actions=(
        A("create_item", "Create item", "Add an item (row) to a board.", "POST", "/v2",
          fields=(F("board_id", "Board ID", required=True, help="The number in the board's URL."),
                  F("item_name", "Item name", required=True)),
          const_body={"query": "mutation($board: ID!, $name: String!) "
                               "{ create_item(board_id: $board, item_name: $name) { id name } }"},
          body={"variables.board": "board_id", "variables.name": "item_name"}, result="data", writes=True),
    ),
)

CLICKUP = Connector(
    id="clickup", name="ClickUp", category="Productivity",
    description="Create and list ClickUp tasks.",
    base_url="https://api.clickup.com/api/v2", auth_prefix=_RAW, key_label="Personal API token",
    key_help="ClickUp → your avatar → Settings → Apps → API Token (starts with pk_).",
    docs_url="https://clickup.com/api",
    actions=(
        A("create_task", "Create task", "Add a task to a list.", "POST", "/list/{list_id}/task",
          fields=(F("list_id", "List ID", required=True, help="From the list's URL."),
                  F("name", "Task name", required=True), F("description", "Description", "text")),
          body={"name": "name", "description": "description"}, writes=True),
        A("list_tasks", "List tasks", "Tasks in a list.", "GET", "/list/{list_id}/task",
          fields=(F("list_id", "List ID", required=True),), result="tasks"),
    ),
)

TYPEFORM = Connector(
    id="typeform", name="Typeform", category="Productivity",
    description="Read your forms and their responses.",
    base_url="https://api.typeform.com", key_label="Personal access token",
    key_help="Typeform → your avatar → Your settings → Personal tokens → Generate (scopes: forms:read, responses:read).",
    docs_url="https://www.typeform.com/developers/responses/",
    actions=(
        A("list_responses", "Get responses", "Latest answers to a form.", "GET", "/forms/{form_id}/responses",
          fields=(F("form_id", "Form ID", required=True, help="From the form URL (…/to/<id>)."), _LIMIT),
          query={"page_size": "limit"}, result="items"),
        A("list_forms", "List forms", "Your forms.", "GET", "/forms", fields=(_LIMIT,),
          query={"page_size": "limit"}, result="items"),
    ),
)

# ------------------------------------------------------------------ DevOps

GITLAB = Connector(
    id="gitlab", name="GitLab", category="DevOps",
    description="Create and list issues on gitlab.com.",
    base_url="https://gitlab.com/api/v4", key_label="Personal access token",
    key_help="GitLab → Preferences → Access tokens → Add new token (scope: api). Self-hosted GitLab is not supported yet.",
    docs_url="https://docs.gitlab.com/ee/api/issues.html",
    actions=(
        A("create_issue", "Create issue", "Open an issue in a project.", "POST", "/projects/{project}/issues",
          fields=(F("project", "Project", required=True, help="ID or full path, e.g. my-group/my-project."),
                  F("title", "Title", required=True), F("description", "Description", "text"),
                  F("labels", "Labels", help="Comma separated, e.g. bug,urgent")),
          body={"title": "title", "description": "description", "labels": "labels"}, writes=True),
        A("list_issues", "List issues", "Issues of a project.", "GET", "/projects/{project}/issues",
          fields=(F("project", "Project", required=True), F("state", "State", enum=("opened", "closed", "all")), _LIMIT),
          query={"state": "state", "per_page": "limit"}),
    ),
)

SENTRY = Connector(
    id="sentry", name="Sentry", category="Observability",
    description="Read unresolved errors from a Sentry project.",
    base_url="https://sentry.io/api/0", key_label="Auth token",
    key_help="Sentry → Settings → Auth Tokens (or an internal integration) with the event:read scope.",
    docs_url="https://docs.sentry.io/api/events/list-a-projects-issues/",
    actions=(
        A("list_issues", "List issues", "Errors of a project (default: unresolved).", "GET",
          "/projects/{organization}/{project}/issues/",
          fields=(F("organization", "Organization slug", required=True), F("project", "Project slug", required=True),
                  F("query", "Search", help="Sentry search, e.g. is:unresolved level:error")),
          query={"query": "query"}),
    ),
)

VERCEL = Connector(
    id="vercel", name="Vercel", category="DevOps",
    description="List Vercel projects and deployments.",
    base_url="https://api.vercel.com", key_label="Access token",
    key_help="vercel.com → Account Settings → Tokens → Create (pick the right scope/team).",
    docs_url="https://vercel.com/docs/rest-api",
    actions=(
        A("list_deployments", "List deployments", "Latest deployments, optionally of one project.", "GET",
          "/v6/deployments", fields=(F("project_id", "Project ID or name"), _LIMIT),
          query={"projectId": "project_id", "limit": "limit"}, result="deployments"),
        A("list_projects", "List projects", "Your projects.", "GET", "/v9/projects", fields=(_LIMIT,),
          query={"limit": "limit"}, result="projects"),
    ),
)

CLOUDFLARE = Connector(
    id="cloudflare", name="Cloudflare (read-only)", category="DevOps",
    description="List your Cloudflare zones and DNS records. Read-only.",
    base_url="https://api.cloudflare.com/client/v4", key_label="API token", ok_field="success",
    error_field="errors",
    key_help="Cloudflare → My Profile → API Tokens → Create token with Zone:Read and DNS:Read only.",
    docs_url="https://developers.cloudflare.com/api/",
    actions=(
        A("list_zones", "List zones", "Your domains on Cloudflare.", "GET", "/zones", fields=(_LIMIT,),
          query={"per_page": "limit"}, result="result"),
        A("list_dns_records", "List DNS records", "DNS records of one zone.", "GET", "/zones/{zone_id}/dns_records",
          fields=(F("zone_id", "Zone ID", required=True, help="Use 'List zones' to find it."),
                  F("name", "Record name", help="Optional, e.g. www.example.com")),
          query={"name": "name"}, result="result"),
    ),
)

# ------------------------------------------------------------------ Marketing

WEBFLOW = Connector(
    id="webflow", name="Webflow", category="Marketing",
    description="Read and add Webflow CMS collection items.",
    base_url="https://api.webflow.com/v2", key_label="API token",
    key_help="Webflow → Site settings → Apps & integrations → API access → Generate token (CMS read/write).",
    docs_url="https://developers.webflow.com/data/reference",
    actions=(
        A("list_items", "List items", "Items of a CMS collection.", "GET", "/collections/{collection_id}/items",
          fields=(F("collection_id", "Collection ID", required=True), _LIMIT), query={"limit": "limit"},
          result="items"),
        A("create_item", "Create item (draft)", "Add a draft item to a collection.", "POST",
          "/collections/{collection_id}/items",
          fields=(F("collection_id", "Collection ID", required=True),
                  F("field_data", "Fields", "object", required=True, help='e.g. {"name": "Hello", "slug": "hello"}')),
          body={"fieldData": "field_data"}, const_body={"isDraft": True}, writes=True),
    ),
)

BRANDFETCH = Connector(
    id="brandfetch", name="Brandfetch", category="Sales",
    description="Look up a company's logo, colours and description by its domain.",
    base_url="https://api.brandfetch.io/v2", key_label="API key",
    key_help="brandfetch.com/developers → sign up → copy your API key.",
    docs_url="https://docs.brandfetch.com/",
    actions=(
        A("get_brand", "Get brand", "Brand details for a domain.", "GET", "/brands/{domain}",
          fields=(F("domain", "Domain", required=True, help="e.g. stripe.com"),)),
    ),
)

# ------------------------------------------------------------------ Search (results go to an AI step or a message)

TAVILY = Connector(
    id="tavily", name="Tavily", category="Search",
    description="Web search built for AI agents.",
    base_url="https://api.tavily.com", key_label="API key",
    key_help="app.tavily.com → API Keys (starts with tvly-).", docs_url="https://docs.tavily.com/",
    actions=(
        A("search", "Search the web", "Top results with short content.", "POST", "/search",
          fields=(F("query", "Search for", required=True), _LIMIT,
                  F("depth", "Depth", enum=("basic", "advanced"), help="advanced = better results, more credits.")),
          body={"query": "query", "max_results": "limit", "search_depth": "depth"}, result="results"),
    ),
)

SERPER = Connector(
    id="serper", name="Serper (Google Search)", category="Search",
    description="Google search results as data.",
    base_url="https://google.serper.dev", auth_header="X-API-KEY", auth_prefix=_RAW, key_label="API key",
    key_help="serper.dev → Dashboard → API key.", docs_url="https://serper.dev/",
    actions=(
        A("search", "Google search", "Organic results.", "POST", "/search",
          fields=(F("query", "Search for", required=True), _LIMIT), body={"q": "query", "num": "limit"},
          result="organic"),
    ),
)

EXA = Connector(
    id="exa", name="Exa", category="Search",
    description="AI search engine for finding pages by meaning.",
    base_url="https://api.exa.ai", auth_header="x-api-key", auth_prefix=_RAW, key_label="API key",
    key_help="dashboard.exa.ai → API Keys.", docs_url="https://docs.exa.ai/",
    actions=(
        A("search", "Search", "Pages matching your query.", "POST", "/search",
          fields=(F("query", "Search for", required=True), _LIMIT), body={"query": "query", "numResults": "limit"},
          result="results"),
    ),
)

FIRECRAWL = Connector(
    id="firecrawl", name="Firecrawl", category="Search",
    description="Turn any web page into clean text (markdown). You are responsible for respecting the site's terms.",
    base_url="https://api.firecrawl.dev", key_label="API key",
    key_help="firecrawl.dev → Dashboard → API Keys (starts with fc-).", docs_url="https://docs.firecrawl.dev/",
    actions=(
        A("scrape", "Read a web page", "Markdown text of one page.", "POST", "/v1/scrape",
          fields=(F("url", "Page address", required=True, help="https://…"),),
          body={"url": "url"}, const_body={"formats": ["markdown"], "onlyMainContent": True}, result="data"),
    ),
)

WIKIPEDIA = Connector(
    id="wikipedia", name="Wikipedia", category="Search",
    description="Search Wikipedia and read article summaries. No key needed.",
    base_url="https://en.wikipedia.org", needs_key=False, key_label="",
    headers={"User-Agent": "AgentForge/1.0 (workflow automation)", "Accept": "application/json"},
    docs_url="https://api.wikimedia.org/wiki/Core_REST_API",
    actions=(
        A("search", "Search articles", "Article titles and snippets.", "GET", "/w/rest.php/v1/search/page",
          fields=(F("query", "Search for", required=True), _LIMIT), query={"q": "query", "limit": "limit"},
          result="pages"),
        A("summary", "Article summary", "Short summary of one article.", "GET", "/api/rest_v1/page/summary/{title}",
          fields=(F("title", "Article title", required=True, help="e.g. Alan_Turing"),)),
    ),
)

MORE_CONNECTORS = (DISCORD, HUBSPOT, ASANA, LINEAR, MONDAY, CLICKUP, TYPEFORM, GITLAB, SENTRY, VERCEL, CLOUDFLARE,
                   WEBFLOW, BRANDFETCH, TAVILY, SERPER, EXA, FIRECRAWL, WIKIPEDIA)
