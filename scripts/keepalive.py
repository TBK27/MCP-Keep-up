#!/usr/bin/env python3
"""
Keepalive ping for the google_workspace_mcp Render deployment.

No OAuth involved at all - just hits a plain, unauthenticated endpoint
often enough that Render never considers the container idle. This is
deliberately simple: every prior failure of this project traced back
to a container restart wiping OAuth state, never to a real problem
with real usage. Removing OAuth from the keepalive removes that whole
failure class, since there's no token here to go stale or die.

Rotates between a couple of real, legitimate, always-public endpoints
so traffic doesn't look like one repeated hardcoded call.

Requires as env var:
    MCP_SERVER_URL - base MCP endpoint, e.g. https://.../mcp
"""

import os
import random
import sys
import urllib.request
import urllib.error

MCP_URL = os.environ.get("MCP_SERVER_URL", "https://google-workspace-mcp-4fbe.onrender.com/mcp")
SERVER_BASE = MCP_URL.rsplit("/mcp", 1)[0]

ENDPOINTS = [
    f"{SERVER_BASE}/health",
    f"{SERVER_BASE}/.well-known/oauth-authorization-server",
    f"{SERVER_BASE}/.well-known/oauth-protected-resource",
]


def ping(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def main():
    url = random.choice(ENDPOINTS)
    print(f"Pinging: {url}")
    try:
        status, body = ping(url)
        print(f"Status: {status}")
        print(f"Response (truncated): {body[:300]}")
        if status >= 400:
            sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Ping failed - HTTPError {e.code}: {body[:300]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
