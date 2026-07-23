#!/usr/bin/env python3
"""
Keepalive ping for the google_workspace_mcp Render deployment.

Picks a random, cheap, read-only MCP tool call each run so traffic
looks organic rather than a fixed health-check pattern. Requires
MCP_SESSION_TOKEN and MCP_SERVER_URL as environment variables
(set as GitHub Actions secrets/vars).
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

# Pool of safe, cheap, read-only, no-side-effect tool calls.
# Each is a (tool_name, arguments) pair.
TOOL_POOL = [
    ("list_calendars", {}),
    ("get_events", {"time_min": "today", "time_max": "today"}),
    ("search_drive_files", {"query": "type:document", "max_results": 3}),
    ("search_gmail_messages", {"query": "in:inbox", "max_results": 3}),
    ("list_tasks", {}),
    ("list_contacts", {"max_results": 3}),
]

def main():
    tool_name, tool_args = random.choice(TOOL_POOL)
    print(f"Selected tool: {tool_name} with args: {tool_args}")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": tool_args,
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {SESSION_TOKEN}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"Status: {resp.status}")
            print(f"Response (truncated): {body[:500]}")
            if resp.status >= 400:
                sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTPError {e.code}: {body[:500]}", file=sys.stderr)
        if e.code == 401:
            print("SESSION TOKEN EXPIRED - needs manual re-auth via local script.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error (server may be spinning up): {e.reason}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
