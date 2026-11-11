/**
 * Role-based help: the same blocks explained with examples from the reader's own job.
 * Rule: simple blocks keep the plain wording from help.ts; only the example, the
 * "where to put it" line and - for harder blocks - the "what it does" line change,
 * using words people in that field already use every day.
 */
"use client";
import { useEffect, useState } from "react";
import type { NodeHelp } from "./help";

export type Role = "general" | "sales" | "marketing" | "tech" | "analyst" | "finance";

export const ROLES: { id: Role; label: string; icon: string }[] = [
  { id: "general", label: "Everyone", icon: "👋" },
  { id: "sales", label: "Sales", icon: "🤝" },
  { id: "marketing", label: "Marketing", icon: "📣" },
  { id: "tech", label: "Tech roles", icon: "💻" },
  { id: "analyst", label: "Analyst", icon: "📊" },
  { id: "finance", label: "Finance", icon: "💰" },
];

type RoleText = Partial<NodeHelp>;

export const ROLE_HELP: Record<Exclude<Role, "general">, Record<string, RoleText>> = {
  // ================================================================ SALES
  sales: {
    start: {
      example: "You press Run with {\"lead\": \"Acme Corp\"} and the next block researches that lead.",
    },
    manual_trigger: {
      example: "A \"Prepare my call notes\" workflow you run before each discovery call.",
    },
    webhook_trigger: {
      what: "Gives the workflow its own address that your CRM or web form can send new leads to. Every new lead starts the workflow automatically.",
      example: "A visitor fills in \"Request a demo\" on your website - the lead lands here and gets a reply within a minute.",
      where: "First block. Paste its address into your CRM or form tool (e.g. \"when a new lead is created, send it here\").",
    },
    schedule_trigger: {
      example: "Every Monday at 9, pull last week's deals and post a pipeline summary for the sales team.",
      where: "First block. Set it to your sales rhythm - daily for follow-ups, weekly for pipeline reviews.",
    },
    chat_trigger: {
      example: "A chatbot on your pricing page that answers product questions and books demos.",
    },
    end: {
      example: "The finished follow-up email waits here, ready for you to copy into your CRM.",
    },
    llm: {
      example: "Turn messy call notes into a clean summary: needs, budget, decision maker, next step.",
      where: "Right after the block that brings in the call notes, email or lead details.",
    },
    agent: {
      example: "A prospect-research helper that looks up the company's website and news, then writes a personal opening line.",
      where: "After a new lead comes in. Give it tools like \"look up company\" so it can research before writing.",
    },
    if: {
      example: "Is the deal over $10,000? Yes → alert the account executive. No → send the standard nurture email.",
      where: "After the lead details are known - use it to qualify leads (hot vs. cold).",
    },
    switch: {
      what: "Routes each lead to the right path based on a rule - like lead routing in a CRM, with more than two choices.",
      example: "Send leads to the North, South or Enterprise team depending on region or company size.",
      where: "After the lead is enriched. Connect each output dot to the team or sequence that should handle it.",
    },
    merge: {
      what: "Combines information from two places into one lead record.",
      example: "Combine the lead's form answers with their company details into one profile before scoring.",
      where: "After two paths that each collect part of the lead's information.",
    },
    wait: {
      example: "Send a demo follow-up, wait 3 days, then send a gentle \"any questions?\" email - a simple follow-up cadence.",
      where: "Between steps of a follow-up sequence.",
    },
    approval: {
      example: "Before a discount over 20% goes into a quote, your sales manager approves it.",
      where: "Right before anything sent to the customer that needs a manager's OK (discounts, contracts).",
    },
    template: {
      example: "\"Hi {{ input.first_name }}, thanks for your interest in {{ input.product }} - are you free Thursday?\"",
      where: "Just before sending an outreach email or LinkedIn message.",
    },
    set: {
      what: "Adds or updates fields on the lead, like editing fields in your CRM.",
      example: "Set {\"stage\": \"Qualified\", \"owner\": \"Priya\"} on every lead that passes the check.",
    },
    http: {
      what: "Connects to another sales tool (your CRM, LinkedIn tools, email tool) to read or update records.",
      example: "Create the new deal in your CRM, or look up a company's size and industry.",
      where: "Wherever you need to read from or save to your CRM. Pick the saved CRM key under Credential.",
    },
    noop: {
      example: "Hold the spot for \"update CRM\" until your CRM connection is ready.",
    },
  },

  // ================================================================ MARKETING
  marketing: {
    start: {
      example: "You press Run with {\"topic\": \"spring sale\"} and the next block drafts campaign ideas.",
    },
    manual_trigger: {
      example: "A \"Draft this week's social posts\" workflow you run every Monday.",
    },
    webhook_trigger: {
      what: "Gives the workflow its own address that your forms, ads or email tools can send new sign-ups to. Every sign-up starts the workflow.",
      example: "Someone downloads your e-book - their details arrive here and they get a welcome email right away.",
      where: "First block. Paste its address into your form, landing-page or ads tool.",
    },
    schedule_trigger: {
      example: "Every Friday, collect the week's campaign results and send a short performance recap.",
      where: "First block. Match it to your content calendar (daily posts, weekly reports).",
    },
    chat_trigger: {
      example: "A website chatbot that answers questions about your offer and collects email sign-ups.",
    },
    end: {
      example: "The finished blog draft or ad copy waits here for you to review.",
    },
    llm: {
      example: "Write 5 subject lines for the newsletter in your brand voice.",
      where: "After the block that gives it the topic, audience or brief.",
    },
    agent: {
      example: "A content helper that checks trending topics, then writes a blog outline and three social posts.",
      where: "After the campaign brief. Give it tools like web search so it can research before writing.",
    },
    if: {
      example: "Did the lead open the last 3 emails? Yes → send a sales offer. No → keep them in the nurture flow.",
      where: "After you know how the person engaged - use it to split your audience.",
    },
    switch: {
      what: "Sends each person down a different path based on their segment - like audience segments in your email tool.",
      example: "Students, small businesses and enterprises each get a different welcome email.",
      where: "After the sign-up details are known. Connect each output dot to the message for that segment.",
    },
    merge: {
      what: "Combines results from different channels into one view.",
      example: "Combine numbers from your ads, email and social tools into one weekly campaign report.",
      where: "After the paths that collect each channel's numbers.",
    },
    wait: {
      example: "Welcome email on day 1, wait 2 days, then a \"best tips\" email - a drip campaign.",
      where: "Between the emails of a drip or nurture campaign.",
    },
    approval: {
      example: "Your brand manager checks the AI-written ad copy before it goes live.",
      where: "Right before anything is published or sent to your audience.",
    },
    template: {
      example: "\"Hi {{ input.first_name }}, here's your 20% code for {{ input.campaign }}!\" - personalised like a mail merge.",
      where: "Just before sending an email, SMS or social post.",
    },
    set: {
      what: "Adds tags or fields to a contact, like tagging contacts in your email tool.",
      example: "Tag every sign-up from the spring campaign with {\"source\": \"spring-sale\"}.",
    },
    http: {
      what: "Connects to your marketing tools (email platform, ads, social, analytics) to read results or post content.",
      example: "Add a new subscriber to your email list, or fetch yesterday's ad spend.",
      where: "Wherever you need data from, or need to send data to, a marketing tool. Pick its saved key under Credential.",
    },
    noop: {
      example: "Keep a spot for \"post to social media\" until that account is connected.",
    },
  },

  // ================================================================ TECH (technical words are fine here)
  tech: {
    start: {
      what: "Entry node. The run's input payload (JSON) becomes its output and is available downstream as $json / $trigger.",
      example: "Run with {\"repo\": \"api\", \"pr\": 42} and pass it to a code-review agent.",
    },
    manual_trigger: {
      example: "An on-demand \"generate release notes\" pipeline you kick off before each deploy.",
    },
    webhook_trigger: {
      what: "Exposes an HTTP endpoint. An incoming POST starts a run with the request body as input; response_mode controls whether it returns 202 immediately or the last node's output.",
      example: "A GitHub webhook on pull_request events triggers an AI code review.",
      where: "Entry node. Publish the graph, then register the URL with the sending service (optionally with an auth header).",
    },
    schedule_trigger: {
      what: "Cron / interval trigger. Fires runs on the schedule without any external call.",
      example: "Cron 0 2 * * * - every night at 02:00, scan error logs and open a summary ticket.",
      where: "Entry node. Use a cron expression or interval_seconds (min 60), then Publish.",
    },
    chat_trigger: {
      example: "An internal dev-help bot that answers questions from your docs and runbooks.",
    },
    end: {
      what: "Terminal node. Its input becomes the run's output (returned to webhook callers in last_node mode).",
      example: "Returns the generated JSON report to the CI job that called the webhook.",
    },
    llm: {
      what: "Single chat-completion call to an OpenAI-compatible endpoint. No tool loop. Set json_output for structured responses.",
      example: "Classify an incoming error log as {\"severity\": \"high\", \"component\": \"auth\"}.",
    },
    agent: {
      what: "LLM tool-calling loop. Tools can be HTTP calls, templates or other agents (run as child workflows). Bounded by max_steps and max_total_tokens.",
      example: "An incident triage agent that queries the status API, reads recent deploys, then proposes a root cause.",
      where: "Mid-pipeline. Wire up tools in its config; enable memory for multi-turn chat sessions.",
    },
    if: {
      what: "Boolean branch. Evaluates an expression and routes to the \"true\" or \"false\" output.",
      example: "{{ $json.status_code >= 500 }} → true: page on-call, false: log and continue.",
    },
    switch: {
      what: "Multi-way branch. Evaluates cases in order; first match wins, otherwise \"default\".",
      example: "Route by $json.event_type: push → CI, issue → triage, release → changelog.",
      where: "After the event is parsed. Each case gets its own output handle.",
    },
    merge: {
      what: "Join node. Waits for all incoming branches, then combines them (append, or combine by key).",
      example: "Fan out to two APIs in parallel, then join the responses by user_id.",
    },
    wait: {
      what: "Durable timer (Temporal). The run sleeps without holding resources and resumes after N seconds.",
      example: "Deploy, wait 300 seconds, then hit the health endpoint before closing the ticket.",
    },
    approval: {
      what: "Human-in-the-loop gate. Pauses the workflow until a signal (approve/reject) or timeout (= rejected).",
      example: "Gate a production migration until a lead engineer approves it.",
      where: "Before any irreversible or prod-impacting step.",
    },
    template: {
      what: "Renders a string template with expression placeholders ({{ ... }}) from upstream data.",
      example: "\"Deploy {{ input.version }} to {{ input.env }} finished in {{ input.duration }}s\"",
    },
    set: {
      what: "Builds or patches a JSON object. keep_input merges the new fields into the incoming payload.",
      example: "Normalize the payload to {\"id\", \"email\", \"plan\"} before calling downstream services.",
    },
    http: {
      what: "Generic HTTP client node (GET/POST/PUT/...) with headers, body, credential-based auth and file responses.",
      example: "POST the generated summary to a Slack incoming webhook, or GET a record from your REST API.",
      where: "Any point that needs an external API call. Attach a credential for bearer/header auth.",
    },
    noop: {
      what: "Pass-through node. Useful as a stub or join point while designing.",
      example: "Stub out the \"write to DB\" step while the endpoint is still being built.",
    },
  },

  // ================================================================ ANALYST
  analyst: {
    start: {
      example: "You press Run with {\"month\": \"2026-10\"} and the next steps build that month's report.",
    },
    manual_trigger: {
      example: "An ad-hoc \"refresh and summarise the sales dataset\" job you run when someone asks for numbers.",
    },
    webhook_trigger: {
      what: "Gives the workflow its own address that another system can send new data to. Each new batch of data starts the workflow.",
      example: "Your data tool sends each finished data export here, and the workflow writes a summary of what changed.",
      where: "First block. Paste its address into the tool that produces the data.",
    },
    schedule_trigger: {
      example: "Every morning at 7, pull yesterday's KPIs and send a short summary before the stand-up.",
      where: "First block. Match it to your reporting cycle (daily, weekly, month-end).",
    },
    chat_trigger: {
      example: "A \"ask the data\" bot where colleagues type questions like \"What were sales in Q3?\"",
    },
    end: {
      example: "The finished summary or table lands here so you can copy it into your report.",
    },
    llm: {
      example: "Explain this week's numbers in plain words: what went up, what went down, and why it matters.",
      where: "After the block that fetches or prepares the data.",
    },
    agent: {
      example: "A research helper that pulls the latest figures, compares them to last month and flags anything unusual.",
      where: "After the data is collected. Give it tools to fetch the extra data it may need.",
    },
    if: {
      example: "Did revenue drop more than 10% vs. last week? Yes → alert the manager. No → just file the report.",
      where: "After the numbers are calculated - use it for thresholds and alerts.",
    },
    switch: {
      what: "Puts each record into one of several groups based on rules - like bucketing or categorising rows.",
      example: "Group customers into High, Medium and Low value based on their yearly spend.",
      where: "After the data is cleaned. Connect each output dot to how that group should be handled.",
    },
    merge: {
      what: "Joins data from two sources into one - like a VLOOKUP or a join on a shared column.",
      example: "Join the sales list with the customer list on customer_id to see sales by region.",
      where: "After the two paths that fetch each data source. Use \"combine by key\" with the shared column.",
    },
    wait: {
      example: "Wait 1 hour for the overnight data load to finish before building the report.",
      where: "Before a step that needs other data to be ready first.",
    },
    approval: {
      example: "A senior analyst checks the figures before the report goes to leadership.",
      where: "Right before a report or dashboard update is shared.",
    },
    template: {
      example: "\"Revenue was {{ input.revenue }} ({{ input.change }}% vs last month). Top region: {{ input.top_region }}.\"",
      where: "Just before sending or saving a report summary.",
    },
    set: {
      what: "Adds, renames or calculates fields - like adding a new column to your data.",
      example: "Add {\"period\": \"Q3\", \"currency\": \"USD\"} to every row before the report.",
    },
    http: {
      what: "Fetches data from (or sends results to) another system, like a database API, BI tool or spreadsheet.",
      example: "Pull the latest numbers from your analytics tool, or push a summary row into a shared sheet.",
      where: "Wherever you need fresh data in, or results out. Pick the saved key under Credential.",
    },
    noop: {
      example: "Hold the place of \"update the dashboard\" until that connection is ready.",
    },
  },

  // ================================================================ FINANCE
  finance: {
    start: {
      example: "You press Run with {\"invoice_no\": \"INV-1042\"} and the next step checks that invoice.",
    },
    manual_trigger: {
      example: "A \"prepare month-end summary\" workflow you run during closing.",
    },
    webhook_trigger: {
      what: "Gives the workflow its own address that your accounting or payment system can send new items to. Each new invoice or payment starts the workflow.",
      example: "Every new supplier invoice from your accounting software arrives here for checking.",
      where: "First block. Paste its address into your accounting or payment tool.",
    },
    schedule_trigger: {
      example: "On the 1st of every month, collect last month's expenses and draft the summary for month-end close.",
      where: "First block. Match it to your finance calendar (daily cash check, monthly close).",
    },
    chat_trigger: {
      example: "An internal bot that answers \"What is our travel expense policy?\" or \"Has invoice INV-1042 been paid?\"",
    },
    end: {
      example: "The checked invoice details or summary wait here, ready to post or file.",
    },
    llm: {
      example: "Read an invoice and pull out supplier, amount, tax, due date and PO number.",
      where: "After the block that brings in the invoice, receipt or statement.",
    },
    agent: {
      example: "A helper that matches each payment to the right invoice and lists anything it can't reconcile.",
      where: "After the transactions come in. Give it tools to look up invoices and POs.",
    },
    if: {
      example: "Is the expense over $5,000? Yes → send to the CFO for approval. No → approve automatically.",
      where: "After the amount is known - use it for spending limits and policy checks.",
    },
    switch: {
      what: "Sends each item to the right path based on rules - like assigning transactions to cost centres.",
      example: "Route expenses to Travel, Software or Office budgets based on the category.",
      where: "After the item is read. Connect each output dot to that category's handling.",
    },
    merge: {
      what: "Combines records from two places so they can be compared - the first step of a reconciliation.",
      example: "Combine bank transactions and ledger entries by reference number to spot mismatches.",
      where: "After the two paths that fetch each set of records. Use \"combine by key\" with the reference number.",
    },
    wait: {
      example: "Send a payment reminder, wait 7 days, then send a second reminder if still unpaid.",
      where: "Between steps of a collections or reminder sequence.",
    },
    approval: {
      example: "A payment over $10,000 needs the finance manager's sign-off before it's released.",
      where: "Right before any payment, journal entry or refund that needs sign-off.",
    },
    template: {
      example: "\"Dear {{ input.customer }}, invoice {{ input.invoice_no }} for {{ input.amount }} is due on {{ input.due_date }}.\"",
      where: "Just before sending a reminder, remittance note or summary.",
    },
    set: {
      what: "Adds or updates fields on a record - like coding a transaction with its GL account.",
      example: "Set {\"gl_account\": \"6100\", \"cost_centre\": \"Marketing\"} on each approved expense.",
    },
    http: {
      what: "Connects to your finance systems (accounting software, bank feed, payment tool) to read or record entries.",
      example: "Fetch today's bank transactions, or create the approved bill in your accounting software.",
      where: "Wherever you need data from, or need to record something in, a finance system. Pick its saved key under Credential.",
    },
    noop: {
      example: "Keep a spot for \"post to the ledger\" until the accounting connection is set up.",
    },
  },
};

/** Help for a block, adjusted to the reader's role (falls back to the plain text). */
export function roleHelp(base: NodeHelp, type: string, role: Role): NodeHelp {
  if (role === "general") return base;
  return { ...base, ...(ROLE_HELP[role][type] ?? {}) };
}

const STORAGE_KEY = "agentforge.role";
const EVENT = "agentforge-role";

/** The reader's chosen role, remembered in this browser and shared across open pages. */
export function useRole(): [Role, (role: Role) => void] {
  const [role, setRoleState] = useState<Role>("general");

  useEffect(() => {
    const read = () => {
      try {
        const saved = localStorage.getItem(STORAGE_KEY) as Role | null;
        if (saved && ROLES.some((r) => r.id === saved)) setRoleState(saved);
      } catch { /* storage blocked - keep default */ }
    };
    read();
    window.addEventListener(EVENT, read);
    window.addEventListener("storage", read);
    return () => {
      window.removeEventListener(EVENT, read);
      window.removeEventListener("storage", read);
    };
  }, []);

  const setRole = (next: Role) => {
    setRoleState(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* ignore */ }
    window.dispatchEvent(new Event(EVENT));
  };
  return [role, setRole];
}
