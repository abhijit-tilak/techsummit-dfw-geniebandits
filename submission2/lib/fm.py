"""Governed foundation-model calls via the Databricks AI Gateway serving endpoint.

Uses databricks-gpt-oss-120b (a governed OSS model served in-workspace, bounded by
the gateway) through the OpenAI-compatible /invocations path. Handles the gpt-oss
response shape (a reasoning block followed by a text block).
"""
from __future__ import annotations

import json
import subprocess
import urllib.request

PROFILE = "rkm-sandbox-1"
HOST = "https://fe-sandbox-rkm-sandbox-1.cloud.databricks.com"
ENDPOINT = "databricks-gpt-oss-120b"


def _token() -> str:
    out = subprocess.check_output(
        ["databricks", "auth", "token", "--profile", PROFILE], text=True
    )
    return json.loads(out)["access_token"]


def _extract(content) -> str:
    if isinstance(content, str):
        return content.strip()
    texts, reasoning = [], []
    for b in content:
        if b.get("type") in ("text", "output_text") and b.get("text"):
            texts.append(b["text"])
        elif b.get("type") == "reasoning":
            for s in b.get("summary", []):
                if s.get("text"):
                    reasoning.append(s["text"])
    return ("\n".join(texts) or "\n".join(reasoning)).strip()


def chat(messages: list[dict], max_tokens: int = 700, temperature: float = 0.2) -> str:
    body = json.dumps(
        {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    ).encode()
    req = urllib.request.Request(
        f"{HOST}/serving-endpoints/{ENDPOINT}/invocations",
        data=body,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return _extract(data["choices"][0]["message"]["content"])
