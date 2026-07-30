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

Retries once with a longer timeout, since a cold-started free-tier
Render container can genuinely take longer than 20s to answer the
very first request after spinning up - that's not a real failure,
just Render waking up.

Requires as env var:
    MCP_SERVER_URL - base MCP endpoint, e.g. https://.../mcp
"""

import os
import random
import sys
import time
import urllib.request
import urllib.error

MCP_URL = os.environ.get("MCP_SERVER_URL", "https://google-workspace-mcp-4fbe.onrender.com/mcp")
SERVER_BASE = MCP_URL.rsplit("/mcp", 1)[0]

ENDPOINTS = [
    f"{SERVER_BASE}/health",
    f"{SERVER_BASE}/.well-known/oauth-authorization-server",
    f"{SERVER_BASE}/.well-known/oauth-protected-resource/mcp",
]


def ping(url, timeout):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def try_ping(url, timeout):
    try:
        status, body = ping(url, timeout)
        print(f"Status: {status}")
        print(f"Response (truncated): {body[:300]}")
        return status < 400
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Ping failed - HTTPError {e.code}: {body[:300]}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        return False


def main():
    url = random.choice(ENDPOINTS)
    print(f"Pinging: {url}")
    if try_ping(url, timeout=20):
        return

    print("First attempt failed - retrying once with a longer timeout in case Render is cold-starting...", file=sys.stderr)
    time.sleep(5)
    if try_ping(url, timeout=60):
        return

    print("Second attempt also failed.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
