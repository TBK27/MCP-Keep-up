#!/usr/bin/env python3
"""
Keepalive ping for the google_workspace_mcp Render deployment.

Does a proper MCP handshake (initialize -> capture session ID -> tools/call)
then picks a random, cheap, read-only tool each run so traffic looks
organic rather than a fixed health-check pattern.

Requires MCP_SESSION_TOKEN and MCP_SERVER_URL as environment variables.
"""

import json
import os
import random
import sys
import urllib.request
import urllib.error

MCP_URL = os.environ.get("MCP_SERVER_URL", "https://google-workspace-mcp-4fbe.onrender.com/mcp")
SESSION_TOKEN = os.environ.get("MCP_SESSION_TOKEN")

if not SESSION_TOKEN:
    print("ERROR: MCP_SESSION_TOKEN not set", file=sys.stderr)
    sys.exit(1)

TOOL_POOL = [
    ("list_calendars", {}),
    ("get_events", {"time_min": "today", "time_max": "today"}),
    ("search_drive_files", {"query": "type:document", "max_results": 3}),
    ("search_gmail_messages", {"query": "in:inbox", "max_results": 3}),
    ("list_tasks", {}),
    ("list_contacts", {"max_results": 3}),
]


def post_json(payload, session_id=None):
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {SESSION_TOKEN}",
    }
    if session_id:
        headers["mcp-session-id"] = session_id

    req = urllib.request.Request(MCP_URL, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        new_session_id = resp.headers.get("mcp-session-id")
        return resp.status, body, new_session_id


def main():
    # Step 1: initialize, to get a session ID
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
        status, body, session_id = post_json(init_payload)
        print(f"Initialize status: {status}")
        if not session_id:
            print(f"WARNING: No session ID returned. Body: {body[:300]}", file=sys.stderr)
            sys.exit(1)
        print(f"Session ID acquired.")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Initialize failed - HTTPError {e.code}: {body[:500]}", file=sys.stderr)
        if e.code == 401:
            print("SESSION TOKEN EXPIRED - needs manual re-auth via local script.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error during initialize: {e.reason}", file=sys.stderr)
        sys.exit(1)

    # Step 2: pick a random tool, call it with the session ID
    tool_name, tool_args = random.choice(TOOL_POOL)
    print(f"Selected tool: {tool_name} with args: {tool_args}")

    call_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_args},
    }

    try:
        status, body, _ = post_json(call_payload, session_id=session_id)
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
