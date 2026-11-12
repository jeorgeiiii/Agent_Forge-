/**
 * Plain-language help shown in the editor and run pages.
 * Rule: everyday words, short sentences, one real example, and where the block goes.
 */

export type NodeHelp = {
  /** one short line under the name in the list of blocks */
  short: string;
  /** what it does, in simple words */
  what: string;
  /** one real-life example */
  example: string;
  /** where to put it in the workflow */
  where: string;
};

export const NODE_HELP: Record<string, NodeHelp> = {
  // ---------------------------------------------------------------- how a workflow starts
  start: {
    short: "Where your workflow begins.",
    what: "Every workflow begins here. It takes the information you give when you press Run and passes it to the next block.",
    example: "You press Run and type {\"name\": \"Ada\"} - the next block can then greet Ada by name.",
    where: "Always the very first block. A workflow can have only one starting block.",
  },
  manual_trigger: {
    short: "Starts when you press the Run button.",
    what: "The workflow starts only when you (or a teammate) press Run. Good for testing and for jobs you do by hand.",
    example: "A \"Write this week's newsletter\" workflow you run yourself every Friday.",
    where: "First block. Use it instead of Start when the workflow is always started by hand.",
  },
  webhook_trigger: {
    short: "Starts when another app sends it a message.",
    what: "Gives your workflow its own web address. When another website or app sends data to that address, the workflow starts with that data.",
    example: "Your online shop sends every new order here, and the workflow emails the customer a thank-you note.",
    where: "First block. After you Publish, copy its address into the other app's settings.",
  },
  schedule_trigger: {
    short: "Starts by itself on a timetable.",
    what: "Runs the workflow automatically again and again - for example every hour or every morning - without anyone pressing a button.",
    example: "Every morning at 8, check yesterday's sales and post a short summary to your team chat.",
    where: "First block. Set how often it should run (e.g. every 3600 seconds = every hour), then Publish.",
  },
  chat_trigger: {
    short: "Starts each time someone sends a chat message.",
    what: "Turns your workflow into a chatbot. Each message a person types starts the workflow, and the answer goes back to them.",
    example: "A help-desk bot on your website that answers \"Where is my order?\"",
    where: "First block. Usually followed by an AI Agent block with memory turned on, so it remembers the chat.",
  },
  end: {
    short: "Where your workflow finishes.",
    what: "Marks the finish line. Whatever reaches this block becomes the final result of the run.",
    example: "After the AI writes an answer, the End block holds that answer so you can see it in the run page.",
    where: "Last block. You can have more than one (one for each path after an IF block).",
  },

  // ---------------------------------------------------------------- AI
  llm: {
    short: "Ask an AI one question and get one answer.",
    what: "Sends your question or instruction to an AI model and gives back its reply. It answers once - it does not use tools.",
    example: "Turn a long customer email into a two-line summary for your support team.",
    where: "Anywhere in the middle, after the block that gives it the text to work on.",
  },
  agent: {
    short: "An AI helper that can use tools to finish a task.",
    what: "An AI that thinks step by step and can use tools you give it (look up the weather, call a website, even ask another AI helper) until the job is done.",
    example: "A travel helper that checks the weather tool, then tells the customer whether to pack an umbrella.",
    where: "In the middle. Give it tools in its settings. Turn on memory if it should remember earlier chat messages.",
  },

  // ---------------------------------------------------------------- deciding and waiting
  if: {
    short: "Choose between two paths: yes or no.",
    what: "Checks a question and sends the work down one of two paths: \"true\" (yes) or \"false\" (no).",
    example: "Is the order over $100? Yes → send a VIP thank-you gift. No → send a normal thank-you email.",
    where: "In the middle. Connect the \"true\" dot to what should happen on yes, and \"false\" to what happens on no.",
  },
  switch: {
    short: "Choose between many paths.",
    what: "Like IF, but with more than two choices. It checks each case in order and follows the first one that matches; if none match it uses \"default\".",
    example: "Send the message to Sales, Support or Billing depending on what the customer asked about.",
    where: "In the middle. Each case gets its own dot on the right - connect each dot to the right team's path.",
  },
  merge: {
    short: "Bring paths back together.",
    what: "Waits for different paths to finish and joins their results into one, so the workflow can continue as one line again.",
    example: "Look up a customer in two places at the same time, then combine both answers into one profile.",
    where: "After two or more paths that should meet again (for example after an IF, or after jobs that run side by side).",
  },
  wait: {
    short: "Pause for a while before continuing.",
    what: "Holds the workflow for the time you choose, then carries on. Nothing is lost while it waits.",
    example: "Wait 2 days after someone signs up, then send them a \"How is it going?\" email.",
    where: "Between the two steps that should have a gap in time.",
  },
  approval: {
    short: "Pause until a person says yes or no.",
    what: "Stops and waits for a human to check things. On the run page someone presses Approve or Reject, and the workflow follows that answer.",
    example: "Before the AI's reply is sent to an important customer, a manager reads it and approves it.",
    where: "Right before any risky step. Connect \"approved\" to what should happen on yes and \"rejected\" to what happens on no.",
  },

  // ---------------------------------------------------------------- data and other apps
  template: {
    short: "Write text that fills in details for you.",
    what: "Builds a piece of text with blanks that are filled in automatically, like a mail-merge letter.",
    example: "\"Hi {{ input.name }}, your order #{{ input.order }} has shipped!\"",
    where: "Anywhere you need a ready-made message, usually just before sending it or showing it.",
  },
  set: {
    short: "Add or change pieces of information.",
    what: "Creates a small set of named values (like a form you fill in) that later blocks can use.",
    example: "Mark every order as {\"priority\": \"high\"} before passing it on to the next step.",
    where: "In the middle, when you want to tidy up, add or rename information before the next block.",
  },
  http: {
    short: "Talk to another website or app.",
    what: "Fetches information from, or sends information to, another online service using its web address.",
    example: "Ask a weather website for today's weather in the customer's city.",
    where: "In the middle, whenever your workflow needs something from the internet. Pick a saved credential if the service needs a key.",
  },
  noop: {
    short: "Does nothing - a placeholder.",
    what: "Passes everything straight through without changing it. Handy while you are still planning a workflow.",
    example: "Put it where a \"send to accounting\" step will go later, so the rest of the workflow already works.",
    where: "Anywhere, as a stand-in for a step you will build later.",
  },
};

// ---- integrations (connect them first on the Integrations page) ----
const CONNECT_FIRST = " Connect it once on the Integrations page, then pick an action and the saved key here.";
Object.assign(NODE_HELP, {
  slack: { short: "Send Slack messages.", what: "Posts a message to a Slack channel, or lists channels." + CONNECT_FIRST,
           example: "Tell #sales when a new lead arrives.", where: "Usually near the end, to tell people something happened." },
  github: { short: "Create and read GitHub issues.", what: "Opens issues, adds comments and reads repositories." + CONNECT_FIRST,
            example: "Open a bug issue when a customer reports a problem.", where: "Anywhere you need to file or read work items." },
  notion: { short: "Add rows to Notion.", what: "Adds pages to a Notion database, searches it, or reads a page." + CONNECT_FIRST,
            example: "Save every meeting summary as a row in the Notes database.", where: "Near the end, to keep a record." },
  airtable: { short: "Read and write Airtable.", what: "Lists, adds and updates records in an Airtable table." + CONNECT_FIRST,
              example: "Add each new signup to the Leads table.", where: "Wherever you read or store rows of data." },
  resend: { short: "Send an email.", what: "Sends one email through Resend from your own domain." + CONNECT_FIRST,
            example: "Email the customer a summary of their order.", where: "Near the end, to notify someone." },
  stripe: { short: "Look up Stripe data (read-only).", what: "Finds customers, payments and your balance. It cannot charge or refund." + CONNECT_FIRST,
            example: "Check whether a customer has paid before replying.", where: "Before a decision that depends on payments." },
} satisfies Record<string, NodeHelp>);

export const FALLBACK_HELP: NodeHelp = {
  short: "A building block for your workflow.",
  what: "This block does one job in your workflow.",
  example: "-",
  where: "Connect it between the blocks that come before and after it.",
};

export function nodeHelp(type: string): NodeHelp {
  return NODE_HELP[type] ?? FALLBACK_HELP;
}

/** Plain explanations for the most common settings (shown under the box). */
export const FIELD_HELP: Record<string, string> = {
  url: "The web address to contact, e.g. https://api.example.com/orders",
  method: "GET = fetch information. POST = send new information. Most of the time GET or POST is right.",
  headers: "Extra details some websites ask for, written as JSON. Usually you can leave this empty.",
  body: "The information to send (for POST). Can include blanks like {{ $json.email }}.",
  body_file: "Send a saved file instead of text, e.g. {{ $json.file }} from an earlier step.",
  response_format: "Choose \"file\" when the answer is a file (a PDF, a picture) so it is saved instead of shown as text.",
  credential_id: "The saved password or key this block should use. Add new ones on the credentials page.",
  auth_type: "How the key is sent. If unsure, keep the default.",
  auth_header_name: "Only if the website asks for its key under a special name, e.g. X-API-Key.",
  response_mode: "immediately = reply \"got it\" right away · last_node = wait and reply with the workflow's final answer.",
  prompt: "What you want the AI to do. You can use blanks like {{ $json.question }} to put in information from earlier steps.",
  system: "Background instructions for the AI, e.g. \"You are a friendly support agent. Keep answers short.\"",
  model: "Which AI model to use (from your AI provider), e.g. llama-3.3-70b-versatile.",
  base_url: "Your AI provider's address. Leave empty to use the one saved with your credential.",
  temperature: "How creative the AI is: 0 = careful and predictable, 1 = more creative.",
  max_tokens: "The longest answer allowed (about 4 letters per token).",
  json_output: "Tick this if you need the AI's answer as structured data instead of plain text.",
  max_steps: "How many think-and-use-a-tool rounds the AI helper may take before it must answer.",
  max_total_tokens: "A spending limit: the helper stops when it has used this much AI text.",
  tools: "The tools this AI helper may use: an integration (Wikipedia, Slack, Notion …), a web call, a template or another AI helper. Use “Add integration tool” below: you choose the action and the key, the AI only fills in the details.",
  allow_writes: "Let this AI helper use tools that send or change things (post to Slack, create a GitHub issue …). Leave it off if the helper should only look things up.",
  memory: "Lets the AI remember earlier messages in the same chat.",
  type: "none = no memory · window = last few messages · buffer = the whole chat · summary = a short summary of older messages.",
  session_id: "Which conversation this belongs to. The chat block fills this in for you.",
  window: "How many recent messages to remember (for \"window\").",
  keep_recent: "How many of the newest messages to keep word for word (for \"summary\").",
  redact_pii: "Hide emails, phone numbers and card numbers before anything is saved.",
  condition: "The yes/no question, e.g. {{ $json.total > 100 }} means \"is the total more than 100?\"",
  cases: "The choices to check, in order. Each needs a name (id) and a question (condition).",
  mode: "How to join the paths: append = put all results in one list (most common).",
  key: "For \"combine by key\": the field that matches records, e.g. email.",
  seconds: "How long to wait, in seconds (3600 = 1 hour, 86400 = 1 day).",
  message: "The note the person sees when asked to approve, e.g. \"Send this reply to the customer?\"",
  timeout_seconds: "If nobody answers in this time, it counts as \"rejected\".",
  template: "Your text with blanks, e.g. Hello {{ input.name }}!",
  fields: "The values to set, as JSON, e.g. {\"status\": \"new\"}",
  keep_input: "Keep the information that came in and add these values to it.",
  cron: "A timetable in cron format, e.g. 0 8 * * * means every day at 8:00.",
  interval_seconds: "Run again every this many seconds (at least 60).",
  retries: "How many times to try again if this step fails.",
  continue_on_fail: "Keep going even if this step fails (the error is passed on instead).",
};

/** Explanations for buttons, shown as a small line or a tooltip. */
export const BUTTON_HELP = {
  save: "Saves your changes. Each save is kept as a version, so you can go back later.",
  run: "Saves, then tries the workflow once with information you type in. You'll see each step happen live.",
  triggers: "Make the workflow live so it starts by itself - from another app, on a timetable, or from a chat.",
  history: "See every time this workflow ran - when, whether it worked, and what it cost.",
  newGraph: "Start a new workflow from a simple 3-block starter.",
  approve: "Say yes - the workflow continues down the \"approved\" path.",
  reject: "Say no - the workflow takes the \"rejected\" path instead.",
  cancel: "Stop this run now. Steps that already finished stay finished.",
  retry: "Run it again, starting from the step that failed. Steps that already worked are not repeated.",
  deleteNode: "Remove this block and its connections from the workflow.",
  workspace: "Switch between your own space and team spaces. Each one has its own workflows and members.",
  formTab: "Fill in this block's settings with simple boxes.",
  jsonTab: "For experts: edit the same settings as raw JSON text.",
};
