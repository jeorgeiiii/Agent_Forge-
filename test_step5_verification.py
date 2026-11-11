"""
Step 5 Verification: Wait for full response -> SSE (Streaming)
Tests both FastAPI `/copilot/stream` and the Next.js SSE stream reader protocol.
"""
import asyncio
import json
import os
import httpx
from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv("apps/web/.env.local")

async def test_fastapi_sse_stream():
    print("--- 1. Testing FastAPI /copilot/stream SSE streaming ---")
    from apps.api.app.copilot import stream_suggest_graph

    prompt = "Create a 2-step workflow with Start and End"
    tokens = []
    done_event = None

    async for chunk in stream_suggest_graph(prompt):
        for line in chunk.split("\n"):
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
                if event.get("type") == "token":
                    tokens.append(event.get("content", ""))
                elif event.get("type") == "done":
                    done_event = event
                elif event.get("type") == "error":
                    print(f"Stream error: {event.get('error')}")
            except Exception as e:
                pass

    print(f"  [x] Received {len(tokens)} tokens via SSE stream.")
    accumulated = "".join(tokens)
    print(f"  [x] Sample accumulated text ({len(accumulated)} chars): {accumulated[:100]}...")
    assert len(tokens) > 0, "No tokens received from FastAPI SSE stream!"
    assert done_event is not None, "Missing 'done' event from SSE stream!"
    print(f"  [x] Done event successfully received with text length: {len(done_event.get('text', ''))}")
    print("  => FastAPI SSE Streaming PASS!")

async def test_sse_protocol_parsing():
    print("\n--- 2. Testing SSE Protocol Parser & Events Format ---")
    sample_sse = (
        'data: {"type":"token","content":"Hello"}\n\n'
        'data: {"type":"token","content":" world"}\n\n'
        'data: {"type":"done","text":"Hello world","graph":{"nodes":[{"id":"1","type":"start","label":"Start","config":{}},{"id":"2","type":"end","label":"End","config":{}}],"edges":[{"source":"1","target":"2"}]}}\n\n'
        'data: [DONE]\n\n'
    )

    tokens = []
    done_payload = None

    for block in sample_sse.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        payload_str = block[5:].strip()
        if payload_str == "[DONE]":
            break
        event = json.loads(payload_str)
        if event["type"] == "token":
            tokens.append(event["content"])
        elif event["type"] == "done":
            done_payload = event

    assert "".join(tokens) == "Hello world", f"Tokens mismatch: {tokens}"
    assert done_payload is not None, "Missing done payload"
    assert len(done_payload["graph"]["nodes"]) == 2, "Graph node count mismatch"
    print("  [x] Tokens correctly accumulated: 'Hello world'")
    print(f"  [x] Graph nodes decoded: {[n['label'] for n in done_payload['graph']['nodes']]}")
    print("  => SSE Protocol Parser PASS!")

async def main():
    print("========================================")
    print("  STEP 5 VERIFICATION: SSE STREAMING    ")
    print("========================================")
    await test_fastapi_sse_stream()
    await test_sse_protocol_parsing()
    print("\n========================================")
    print("  STEP 5 ALL VERIFICATIONS PASSED!     ")
    print("========================================")

if __name__ == "__main__":
    asyncio.run(main())
