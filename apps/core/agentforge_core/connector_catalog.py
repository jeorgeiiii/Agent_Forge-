"""The built-in integrations (Phase 1: services that use an API key or token).

Adding a service = adding one Connector below + a test case. Field names become the node's settings;
see connectors.py for the request template rules. Keep actions small and well-labelled.
API references: each connector's docs_url.
"""

from .connectors import Connector, ConnectorAction as A, ConnectorField as F, register_connector

_LIMIT = F("limit", "How many", "integer", help="Maximum number of results (leave empty for the service default).")

SLACK = Connector(
    id="slack", name="Slack", category="Communication",
    description="Send messages to Slack channels and list channels.",
    base_url="https://slack.com/api", ok_field="ok", error_field="error",
    key_label="Bot token",
    key_help="Slack → Your apps → (your app) → OAuth & Permissions → Bot User OAuth Token (starts with xoxb-). "
             "Give the bot chat:write and channels:read, then invite it to the channel.",
    docs_url="https://api.slack.com/methods",
    actions=(
        A("send_message", "Send message", "Post a message to a channel.", "POST", "/chat.postMessage",
          fields=(F("channel", "Channel", required=True, help="Channel ID (C0123…) or #name the bot is in."),
                  F("text", "Message", "text", required=True),
                  F("thread_ts", "Reply in thread", help="Optional: the ts of the message to reply to.")),
          body={"channel": "channel", "text": "text", "thread_ts": "thread_ts"}, writes=True),
        A("list_channels", "List channels", "Public channels in the workspace.", "GET", "/conversations.list",
          fields=(_LIMIT,), query={"limit": "limit"}, const_query={"exclude_archived": True}, result="channels"),
    ),
)

GITHUB = Connector(
    id="github", name="GitHub", category="DevOps",
    description="Create issues, comment and read repositories on GitHub.",
    base_url="https://api.github.com", key_label="Personal access token",
    headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "AgentForge"},
    key_help="GitHub → Settings → Developer settings → Fine-grained tokens. "
             "Pick only the repositories needed, with Issues: read/write.",
    docs_url="https://docs.github.com/en/rest",
    actions=(
        A("create_issue", "Create issue", "Open a new issue.", "POST", "/repos/{owner}/{repo}/issues",
          fields=(F("owner", "Owner", required=True, help="User or organisation, e.g. octocat."),
                  F("repo", "Repository", required=True, help="Repository name, e.g. hello-world."),
                  F("title", "Title", required=True), F("body", "Description", "text"),
                  F("labels", "Labels", "array", help='e.g. ["bug"]')),
          body={"title": "title", "body": "body", "labels": "labels"}, writes=True),
        A("comment_issue", "Comment on issue", "Add a comment to an issue or pull request.", "POST",
          "/repos/{owner}/{repo}/issues/{issue_number}/comments",
          fields=(F("owner", "Owner", required=True), F("repo", "Repository", required=True),
                  F("issue_number", "Issue number", "integer", required=True),
                  F("body", "Description", "text", required=True)),
          body={"body": "body"}, writes=True),
        A("list_issues", "List issues", "Open issues of a repository.", "GET", "/repos/{owner}/{repo}/issues",
          fields=(F("owner", "Owner", required=True), F("repo", "Repository", required=True),
                  F("state", "State", enum=("open", "closed", "all")), _LIMIT),
          query={"state": "state", "per_page": "limit"}),
        A("get_repo", "Get repository", "Details of one repository.", "GET", "/repos/{owner}/{repo}",
          fields=(F("owner", "Owner", required=True), F("repo", "Repository", required=True))),
    ),
)

NOTION = Connector(
    id="notion", name="Notion", category="Productivity",
    description="Add pages to Notion databases and read them.",
    base_url="https://api.notion.com/v1", key_label="Integration secret",
    headers={"Notion-Version": "2022-06-28"},
    key_help="notion.so/my-integrations → New integration → copy the Internal Integration Secret. "
             "Then open the database in Notion → ••• → Connections → add your integration.",
    docs_url="https://developers.notion.com/reference",
    actions=(
        A("create_page", "Add page to database", "Create a row/page in a database.", "POST", "/pages",
          fields=(F("database_id", "Database ID", required=True, help="From the database URL (32 characters)."),
                  F("properties", "Properties", "object", required=True,
                    help='Notion property values, e.g. {"Name": {"title": [{"text": {"content": "Hello"}}]}}')),
          body={"parent.database_id": "database_id", "properties": "properties"}, writes=True),
        A("query_database", "Search database", "Rows of a database, optionally filtered.", "POST",
          "/databases/{database_id}/query",
          fields=(F("database_id", "Database ID", required=True),
                  F("filter", "Filter", "object", help="Optional Notion filter object."), _LIMIT),
          body={"filter": "filter", "page_size": "limit"}, result="results"),
        A("get_page", "Get page", "One page and its properties.", "GET", "/pages/{page_id}",
          fields=(F("page_id", "Page ID", required=True),)),
    ),
)

AIRTABLE = Connector(
    id="airtable", name="Airtable", category="Databases",
    description="Read, add and update Airtable records.",
    base_url="https://api.airtable.com/v0", key_label="Personal access token",
    key_help="airtable.com/create/tokens → scopes data.records:read and data.records:write, "
             "access only to the bases you need.",
    docs_url="https://airtable.com/developers/web/api/introduction",
    actions=(
        A("list_records", "List records", "Records of a table, optionally filtered by a formula.", "GET",
          "/{base_id}/{table}",
          fields=(F("base_id", "Base ID", required=True, help="Starts with app… (from the base URL)."),
                  F("table", "Table", required=True, help="Table name or ID."),
                  F("formula", "Filter formula", help="Optional, e.g. {Status} = 'Done'"), _LIMIT),
          query={"filterByFormula": "formula", "maxRecords": "limit"}, result="records"),
        A("create_record", "Create record", "Add one record.", "POST", "/{base_id}/{table}",
          fields=(F("base_id", "Base ID", required=True), F("table", "Table", required=True),
                  F("fields", "Fields", "object", required=True, help='e.g. {"Name": "Ada", "Score": 9}')),
          body={"fields": "fields"}, const_body={"typecast": True}, writes=True),
        A("update_record", "Update record", "Change some fields of a record.", "PATCH",
          "/{base_id}/{table}/{record_id}",
          fields=(F("base_id", "Base ID", required=True), F("table", "Table", required=True),
                  F("record_id", "Record ID", required=True, help="Starts with rec…"),
                  F("fields", "Fields", "object", required=True)),
          body={"fields": "fields"}, const_body={"typecast": True}, writes=True),
    ),
)

RESEND = Connector(
    id="resend", name="Resend", category="Email",
    description="Send emails with Resend.",
    base_url="https://api.resend.com", key_label="API key",
    key_help="resend.com → API Keys → Create (Sending access). Verify your sending domain first.",
    docs_url="https://resend.com/docs/api-reference/emails/send-email",
    actions=(
        A("send_email", "Send email", "Send one email.", "POST", "/emails",
          fields=(F("from", "From", required=True, help="e.g. AgentForge <bot@yourdomain.com> (verified domain)."),
                  F("to", "To", required=True, help="Email address (or several, comma separated)."),
                  F("subject", "Subject", required=True), F("html", "Body (HTML)", "text"),
                  F("text", "Body (plain text)", "text"), F("reply_to", "Reply to")),
          body={"from": "from", "to": "to", "subject": "subject", "html": "html", "text": "text",
                "reply_to": "reply_to"}, writes=True),
    ),
)

STRIPE = Connector(
    id="stripe", name="Stripe (read-only)", category="Commerce",
    description="Look up customers, payments and balance. Read-only: it can't charge or refund.",
    base_url="https://api.stripe.com/v1", key_label="Restricted key",
    key_help="Stripe Dashboard → Developers → API keys → Create restricted key with READ access only "
             "(Customers, PaymentIntents, Balance). Never paste your secret sk_live key here.",
    docs_url="https://docs.stripe.com/api",
    actions=(
        A("list_customers", "Find customers", "Customers, optionally by email.", "GET", "/customers",
          fields=(F("email", "Email", help="Optional exact email."), _LIMIT),
          query={"email": "email", "limit": "limit"}, result="data"),
        A("list_payments", "List payments", "Recent payments (PaymentIntents).", "GET", "/payment_intents",
          fields=(F("customer", "Customer ID", help="Optional cus_…"), _LIMIT),
          query={"customer": "customer", "limit": "limit"}, result="data"),
        A("get_balance", "Get balance", "Available and pending balance.", "GET", "/balance"),
    ),
)

from .connector_catalog_more import MORE_CONNECTORS  # noqa: E402 - Discord, HubSpot, Linear, search tools ...

CONNECTORS = (SLACK, GITHUB, NOTION, AIRTABLE, RESEND, STRIPE, *MORE_CONNECTORS)


def register_builtin_connectors() -> None:
    for connector in CONNECTORS:
        register_connector(connector)
