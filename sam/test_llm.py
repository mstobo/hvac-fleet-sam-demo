#!/usr/bin/env python3
"""
Test LiteLLM endpoint compatibility with SAM.
Tests both streaming and non-streaming modes.
"""

import json
import os
import sys

import requests

API_BASE = os.getenv("LLM_SERVICE_ENDPOINT", "https://lite-llm.mymaas.net")
API_KEY = os.getenv("LLM_SERVICE_API_KEY", "")
MODEL = os.getenv("LLM_SERVICE_GENERAL_MODEL_NAME", "openai/claude-opus-4-5-20251101")
# When your LiteLLM team only allows a model group (e.g. production-models), set this
# or put the group name directly in LLM_SERVICE_GENERAL_MODEL_NAME.
FALLBACK_MODEL = os.getenv("LLM_TEAM_MODEL_GROUP", "production-models")


def _print_access_denied_help(err_body: str) -> None:
    if "team_model_access_denied" not in err_body and "team not allowed" not in err_body:
        return
    print()
    print("Hint: your API key is restricted to a LiteLLM model group, not individual")
    print("      proxy names like openai_proxy/azure-gpt-5-mini.")
    print(f"      Set in sam/.env:")
    print(f'        LLM_SERVICE_GENERAL_MODEL_NAME="{FALLBACK_MODEL}"')
    print(f'        LLM_SERVICE_PLANNING_MODEL_NAME="{FALLBACK_MODEL}"')
    print("      Then: python test_llm.py && ./start_demo_stack.sh --fresh")


def _chat_completion(model: str, stream: bool) -> requests.Response:
    return requests.post(
        f"{API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Say 'hello' and nothing else."}],
            "stream": stream,
            "max_tokens": 512,
        },
        timeout=60,
        stream=stream,
    )


def _models_to_try() -> list[str]:
    seen: set[str] = set()
    order: list[str] = []
    for name in (MODEL, FALLBACK_MODEL):
        if name and name not in seen:
            seen.add(name)
            order.append(name)
    return order


print("=" * 60)
print("LiteLLM Endpoint Test")
print("=" * 60)
print(f"API Base: {API_BASE}")
print(f"Model (from .env): {MODEL}")
if FALLBACK_MODEL and FALLBACK_MODEL != MODEL:
    print(f"Fallback group: {FALLBACK_MODEL}")
if not API_KEY:
    print("Set LLM_SERVICE_API_KEY in sam/.env (see .env.example).", file=sys.stderr)
    sys.exit(1)
print(f"API Key: {API_KEY[:10]}...")
print()

working_model: str | None = None

for candidate in _models_to_try():
    print("Test 1: Non-streaming chat completion")
    print("-" * 40)
    print(f"Trying model: {candidate}")
    try:
        response = _chat_completion(candidate, stream=False)
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
                print("❌ Non-streaming: FAILED (200 but empty content — raise max_tokens)")
            else:
                print("✅ Non-streaming: PASSED")
                working_model = candidate
            break
        err = response.text
        print(f"Error: {err}")
        _print_access_denied_help(err)
        print("❌ Non-streaming: FAILED")
        if candidate == _models_to_try()[-1]:
            sys.exit(1)
        print(f"\nRetrying with fallback model {FALLBACK_MODEL}...\n")
    except Exception as e:
        print(f"❌ Non-streaming: FAILED - {e}")
        if candidate == _models_to_try()[-1]:
            sys.exit(1)

if not working_model:
    sys.exit(1)

print()
print("Test 2: Streaming chat completion")
print("-" * 40)
try:
    response = _chat_completion(working_model, stream=True)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("Streaming chunks received:")
        chunk_count = 0
        for line in response.iter_lines():
            if line:
                line_str = line.decode("utf-8")
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
        print("❌ Streaming: FAILED (SAM uses stream: false — non-streaming pass is enough)")
except Exception as e:
    print(f"❌ Streaming: FAILED - {e}")

print()
print("Test 3: List models endpoint")
print("-" * 40)
try:
    response = requests.get(
        f"{API_BASE}/models",
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10,
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
if working_model != MODEL:
    print(f"Use this model in sam/.env (tests passed with {working_model!r}):")
    print(f'  LLM_SERVICE_GENERAL_MODEL_NAME="{working_model}"')
    print(f'  LLM_SERVICE_PLANNING_MODEL_NAME="{working_model}"')
    print("Then restart: ./start_demo_stack.sh --fresh")
else:
    print("LLM smoke test passed with your current .env model names.")
    print("Restart SAM if Automated Fleet Analysis still fails: ./start_demo_stack.sh --fresh")
