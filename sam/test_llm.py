#!/usr/bin/env python3
"""
Test LiteLLM endpoint compatibility with SAM.
Tests both streaming and non-streaming modes.
"""

import os
import json
import requests

# Load from environment. LLM_SERVICE_API_KEY is required (no fallback — never bake secrets into source).
API_BASE = os.getenv("LLM_SERVICE_ENDPOINT", "https://lite-llm.mymaas.net")
try:
    API_KEY = os.environ["LLM_SERVICE_API_KEY"]
except KeyError:
    raise SystemExit("LLM_SERVICE_API_KEY is not set. Source sam/.env or export it before running.")
MODEL = os.getenv("LLM_SERVICE_GENERAL_MODEL_NAME", "openai/claude-opus-4-5-20251101")

print("=" * 60)
print("LiteLLM Endpoint Test")
print("=" * 60)
print(f"API Base: {API_BASE}")
print(f"Model: {MODEL}")
print(f"API Key: {API_KEY[:10]}...")
print()

# Test 1: Non-streaming request
print("Test 1: Non-streaming chat completion")
print("-" * 40)
try:
    response = requests.post(
        f"{API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "user", "content": "Say 'hello' and nothing else."}
            ],
            "stream": False,
            # Reasoning models (e.g. azure-gpt-5-mini) may use most of a small budget on
            # reasoning_tokens, leaving message.content empty — use >= 256 for smoke tests.
            "max_tokens": 512,
        },
        timeout=60,
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        choice = data.get("choices", [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        usage = data.get("usage") or {}
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        print(f"Response: {content!r}")
        print(f"finish_reason: {finish}")
        if reasoning is not None:
            print(f"reasoning_tokens: {reasoning}")
        if not (content and str(content).strip()):
            print("❌ Non-streaming: FAILED (200 but empty content — raise max_tokens or use azure-gpt-5)")
        else:
            print("✅ Non-streaming: PASSED")
    else:
        print(f"Error: {response.text}")
        print("❌ Non-streaming: FAILED")
except Exception as e:
    print(f"❌ Non-streaming: FAILED - {e}")

print()

# Test 2: Streaming request
print("Test 2: Streaming chat completion")
print("-" * 40)
try:
    response = requests.post(
        f"{API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "user", "content": "Say 'hello' and nothing else."}
            ],
            "stream": True,
            "max_tokens": 512,
        },
        stream=True,
        timeout=60,
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("Streaming chunks received:")
        chunk_count = 0
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith("data: "):
                    chunk_count += 1
                    if chunk_count <= 3:
                        print(f"  Chunk {chunk_count}: {line_str[:80]}...")
        print(f"Total chunks: {chunk_count}")
        if chunk_count > 0:
            print("✅ Streaming: PASSED")
        else:
            print("⚠️ Streaming: No chunks received")
    else:
        print(f"Error: {response.text}")
        print("❌ Streaming: FAILED")
except Exception as e:
    print(f"❌ Streaming: FAILED - {e}")

print()

# Test 3: Check /models endpoint
print("Test 3: List models endpoint")
print("-" * 40)
try:
    response = requests.get(
        f"{API_BASE}/models",
        headers={
            "Authorization": f"Bearer {API_KEY}"
        },
        timeout=10
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        models = data.get("data", [])
        print(f"Available models: {len(models)}")
        for m in models[:5]:
            print(f"  - {m.get('id', 'unknown')}")
        if len(models) > 5:
            print(f"  ... and {len(models) - 5} more")
        print("✅ Models endpoint: PASSED")
    else:
        print(f"Response: {response.text[:200]}")
        print("⚠️ Models endpoint: May not be available")
except Exception as e:
    print(f"⚠️ Models endpoint: {e}")

print()
print("=" * 60)
print("Summary")
print("=" * 60)
print("""
If non-streaming passes but streaming fails:
  → Add 'stream: false' to your model configs (already done)
  → The SAM error might be about A2A protocol, not LLM streaming

If both pass:
  → The LLM endpoint is compatible
  → The 'sendSubscribe' error is about SAM's A2A task subscription
  → This is a SAM/Solace protocol issue, not an LLM issue
""")
