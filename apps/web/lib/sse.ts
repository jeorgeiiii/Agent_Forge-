// Server-Sent Events (SSE) stream consumer utility for real-time AI responses

export type StreamEvent =
  | { type: "token"; content: string }
  | { type: "done"; text: string; graph?: any; explanation?: string }
  | { type: "error"; error: string };

export async function readSSEStream(
  response: Response,
  onToken: (token: string) => void,
  onDone: (data: { text: string; graph?: any }) => void,
  onError: (error: string) => void
): Promise<void> {
  if (!response.body) {
    onError("No response body available for streaming.");
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data:")) continue;

        const dataStr = trimmed.slice(5).trim();
        if (dataStr === "[DONE]") {
          return;
        }

        try {
          const parsed = JSON.parse(dataStr) as StreamEvent;
          if (parsed.type === "token") {
            onToken(parsed.content);
          } else if (parsed.type === "done") {
            onDone({ text: parsed.text, graph: parsed.graph });
            return;
          } else if (parsed.type === "error") {
            onError(parsed.error);
            return;
          }
        } catch {
          // Plain text chunk fallback
          onToken(dataStr);
        }
      }
    }
  } catch (err) {
    onError(err instanceof Error ? err.message : "Error reading SSE stream.");
  } finally {
    reader.releaseLock();
  }
}
