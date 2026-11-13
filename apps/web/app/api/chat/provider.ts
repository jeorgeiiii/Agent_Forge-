/**
 * Calls the AI provider (Groq) for the editor Copilot and turns its errors into plain messages.
 *
 * - "Too many requests" (429) with a short wait (<= 12 s): waits once and tries again automatically.
 * - Every other error: a friendly sentence for the chat. The provider's raw text (organisation IDs,
 *   upgrade links, quota numbers) is only written to the server log, never shown to users.
 */

const GROQ_URL = "https://api.groq.com/openai/v1/chat/completions";
const MAX_AUTO_WAIT_SECONDS = 12;

/** Seconds the provider asks us to wait: Retry-After header, or "try again in 8.75s" in the message. */
function waitSeconds(response: Response, message: string): number | null {
  const header = Number(response.headers.get("retry-after"));
  if (Number.isFinite(header) && header > 0) return header;
  const match = /try again in ([\d.]+)\s*(ms|s)/i.exec(message);
  if (!match) return null;
  return match[2].toLowerCase() === "ms" ? Number(match[1]) / 1000 : Number(match[1]);
}

async function readError(response: Response): Promise<string> {
  const body = (await response.clone().json().catch(() => null)) as { error?: { message?: string } } | null;
  return body?.error?.message ?? "";
}

/** POSTs to Groq; retries once after a short rate-limit wait. Returns the final Response (ok or not). */
export async function callProvider(apiKey: string, payload: unknown, timeoutMs = 45000): Promise<Response> {
  const send = () => fetch(GROQ_URL, {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const first = await send();
  if (first.status !== 429) return first;
  const wait = waitSeconds(first, await readError(first));
  if (wait === null || wait > MAX_AUTO_WAIT_SECONDS) return first;
  await new Promise((resolve) => setTimeout(resolve, Math.ceil(wait * 1000) + 300));
  return send();
}

/** A failed provider response -> {message for the chat, HTTP status}. Logs the raw details server-side. */
export async function friendlyProviderError(response: Response, model: string): Promise<{ error: string; status: number }> {
  const raw = await readError(response);
  console.error("AI provider request failed:", response.status, raw);
  const lower = raw.toLowerCase();
  if (response.status === 429) {
    const wait = waitSeconds(response, raw);
    const daily = lower.includes("per day") || lower.includes("(tpd)") || lower.includes("(rpd)");
    return {
      error: daily
        ? "The AI assistant has used up today's free allowance. It will work again later, or the admin can upgrade the AI plan."
        : `The AI assistant is busy right now (too many requests this minute). Please wait ${wait ? Math.ceil(wait) + " seconds" : "a moment"} and send again.`,
      status: 429,
    };
  }
  if (response.status === 401 || response.status === 403) {
    return { error: "The AI assistant's key is not valid. Ask the admin to check GROQ_API_KEY.", status: 502 };
  }
  if (lower.includes("model") && (lower.includes("does not exist") || lower.includes("do not have access") || lower.includes("decommissioned"))) {
    return { error: `The AI model "${model}" is not available. Ask the admin to set GROQ_MODEL to an active model.`, status: 502 };
  }
  if (response.status === 413 || lower.includes("context") || lower.includes("too large")) {
    return { error: "This conversation got too long for the AI. Start a new chat or shorten your message.", status: 413 };
  }
  if (response.status >= 500) {
    return { error: "The AI service is having trouble right now. Please try again in a minute.", status: 502 };
  }
  return { error: "The AI assistant couldn't answer that. Please try again.", status: 502 };
}
