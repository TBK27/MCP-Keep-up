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

MCP_URL = os.environ.get("MCP_SERVER_URL", "https://google-workspace-mcp-4fbe.onrender.com/mcp")
SERVER_BASE = MCP_URL.rsplit("/mcp", 1)[0]
TOKEN_URL = f"{SERVER_BASE}/token"

REFRESH_TOKEN = os.environ.get("MCP_REFRESH_TOKEN")
CLIENT_ID = os.environ.get("MCP_CLIENT_ID")

if not REFRESH_TOKEN or not CLIENT_ID:
    print("ERROR: MCP_REFRESH_TOKEN and MCP_CLIENT_ID must be set.", file=sys.stderr)
    sys.exit(1)

TOOL_POOL = [
    ("list_calendars", {}),
    ("get_events", {"time_min": "today", "time_max": "today"}),
    ("search_drive_files", {"query": "type:document", "max_results": 3}),
    ("search_gmail_messages", {"query": "in:inbox", "max_results": 3}),
    ("list_tasks", {}),
    ("list_contacts", {"max_results": 3}),
]


def get_fresh_access_token() -> str:
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
        if not access_token:
            raise RuntimeError(f"No access_token in refresh response: {body}")
        return access_token


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


def main():
    try:
        print("Refreshing access token...")
        access_token = get_fresh_access_token()
        print("Got fresh access token.")
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
