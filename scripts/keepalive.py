#!/usr/bin/env python3
"""
Keepalive ping for the google_workspace_mcp Render deployment.

Uses a long-lived refresh token to mint a fresh access token every run
(instead of relying on a 1-hour access token going stale), then does a
proper MCP handshake (initialize -> session ID -> tools/call) and hits
a random, cheap, read-only tool so traffic looks organic.

Requires as env vars:
    MCP_REFRESH_TOKEN - long-lived refresh token from reauth.py
    MCP_CLIENT_ID      - the OAuth client_id reauth.py registered
    MCP_SERVER_URL     - base MCP endpoint, e.g. https://.../mcp
"""

import json
import os
import random
import sys
import urllib.request
import urllib.error
import urllib.parse
import base64
import httpx
from nacl import encoding, public

MCP_URL = os.environ.get("MCP_SERVER_URL", "https://google-workspace-mcp-4fbe.onrender.com/mcp")
SERVER_BASE = MCP_URL.rsplit("/mcp", 1)[0]
TOKEN_URL = f"{SERVER_BASE}/token"

REFRESH_TOKEN = os.environ.get("MCP_REFRESH_TOKEN")
CLIENT_ID = os.environ.get("MCP_CLIENT_ID")
GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_REPO = os.environ.get("GITHUB_REPO")

if not REFRESH_TOKEN or not CLIENT_ID:
    print("ERROR: MCP_REFRESH_TOKEN and MCP_CLIENT_ID must be set.", file=sys.stderr)
    sys.exit(1)

TOOL_POOL = [
    ("list_calendars", {}),
    ("get_events", {}),
    ("search_drive_files", {"query": "type:document"}),
    ("search_gmail_messages", {"query": "in:inbox"}),
    ("list_tasks", {}),
    ("list_contacts", {}),
]


def get_fresh_access_token():
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": CLIENT_ID,
    }).encode("utf-8")

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        access_token = body.get("access_token")
        new_refresh_token = body.get("refresh_token")
        if not access_token:
            raise RuntimeError(f"No access_token in refresh response: {body}")
        return access_token, new_refresh_token


def post_json(url, payload, access_token, session_id=None):
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
    }
    if session_id:
        headers["mcp-session-id"] = session_id

    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        new_session_id = resp.headers.get("mcp-session-id")
        return resp.status, body, new_session_id


def push_secret_to_github(token_value: str, secret_name: str):
    if not GITHUB_PAT or not GITHUB_REPO:
        print("WARNING: GITHUB_PAT/GITHUB_REPO not set, cannot rotate refresh token secret.", file=sys.stderr)
        return
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client() as client:
        key_resp = client.get(f"https://api.github.com/repos/{GITHUB_REPO}/actions/secrets/public-key", headers=headers)
        key_resp.raise_for_status()
        key_data = key_resp.json()
        public_key = public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(token_value.encode("utf-8"))
        encrypted_b64 = base64.b64encode(encrypted).decode("utf-8")
        put_resp = client.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/secrets/{secret_name}",
            headers=headers,
            json={"encrypted_value": encrypted_b64, "key_id": key_data["key_id"]},
        )
        put_resp.raise_for_status()
        print(f"Rotated refresh token pushed to {secret_name}.")


def main():
    try:
        print("Refreshing access token...")
        access_token, new_refresh_token = get_fresh_access_token()
        print("Got fresh access token.")
        if new_refresh_token and new_refresh_token != REFRESH_TOKEN:
            print("Refresh token was rotated by server - updating GitHub secret...")
            push_secret_to_github(new_refresh_token, "MCP_REFRESH_TOKEN")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Token refresh failed - HTTPError {e.code}: {body[:500]}", file=sys.stderr)
        if e.code in (400, 401):
            print("REFRESH TOKEN MAY BE DEAD - needs manual re-auth via local script.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Token refresh failed: {e}", file=sys.stderr)
        sys.exit(1)

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp-keepalive", "version": "1.0"},
        },
    }

    try:
        status, body, session_id = post_json(MCP_URL, init_payload, access_token)
        print(f"Initialize status: {status}")
        if not session_id:
            print(f"WARNING: No session ID returned. Body: {body[:300]}", file=sys.stderr)
            sys.exit(1)
        print("Session ID acquired.")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Initialize failed - HTTPError {e.code}: {body[:500]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error during initialize: {e.reason}", file=sys.stderr)
        sys.exit(1)

    tool_name, tool_args = random.choice(TOOL_POOL)
    print(f"Selected tool: {tool_name} with args: {tool_args}")

    call_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_args},
    }

    try:
        status, body, _ = post_json(MCP_URL, call_payload, access_token, session_id=session_id)
        print(f"Tool call status: {status}")
        print(f"Response (truncated): {body[:500]}")
        if status >= 400:
            sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Tool call failed - HTTPError {e.code}: {body[:500]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error during tool call: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
