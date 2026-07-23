#!/usr/bin/env python3
"""
Script B (manual flow): Local re-auth tool for the MCP Keepalive system.

Drives the OAuth 2.1 + PKCE flow directly against confirmed endpoints
(bypassing the SDK's generic discovery, which was hanging):

  Authorize: https://google-workspace-mcp-4fbe.onrender.com/authorize
  Token:     https://google-workspace-mcp-4fbe.onrender.com/token
  Register:  https://google-workspace-mcp-4fbe.onrender.com/register

Needs a local .env file (gitignored) with:
    GITHUB_PAT=github_pat_xxxx
    GITHUB_REPO=TBK27/MCP-Keep-up
"""

import asyncio
import base64
import hashlib
import os
import secrets
import string
import sys
import webbrowser
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

load_dotenv()

SERVER_BASE = "https://google-workspace-mcp-4fbe.onrender.com"
AUTHORIZE_URL = f"{SERVER_BASE}/authorize"
TOKEN_URL = f"{SERVER_BASE}/token"
REGISTER_URL = f"{SERVER_BASE}/register"

CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"

GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_REPO = os.environ.get("GITHUB_REPO")

if not GITHUB_PAT or not GITHUB_REPO:
    print("ERROR: GITHUB_PAT and GITHUB_REPO must be set in .env", file=sys.stderr)
    sys.exit(1)


def generate_pkce():
    verifier = "".join(secrets.choice(string.ascii_letters + string.digits + "-._~") for _ in range(128))
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class CallbackResult:
    def __init__(self):
        self.code = None
        self.state = None
        self.error = None
        self.event = asyncio.Event()


async def run_callback_server(result: CallbackResult):
    from aiohttp import web

    async def handle(request):
        result.code = request.query.get("code")
        result.state = request.query.get("state")
        result.error = request.query.get("error")
        result.event.set()
        if result.error:
            return web.Response(text=f"Auth failed: {result.error}", content_type="text/plain")
        return web.Response(text="Auth complete - you can close this tab.", content_type="text/plain")

    app = web.Application()
    app.router.add_get(CALLBACK_PATH, handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", CALLBACK_PORT)
    await site.start()
    return runner


def push_secret_to_github(token_value: str, secret_name: str = "MCP_SESSION_TOKEN"):
    from nacl import encoding, public

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
        print(f"\nSuccess: {secret_name} updated on {GITHUB_REPO}. (status {put_resp.status_code})")


async def main():
    async with httpx.AsyncClient(timeout=15) as client:
        # 1. Dynamically register a client
        print("Registering OAuth client...")
        reg_resp = await client.post(
            REGISTER_URL,
            json={
                "client_name": "MCP Keepalive Reauth Tool",
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        reg_resp.raise_for_status()
        client_info = reg_resp.json()
        client_id = client_info["client_id"]
        print(f"Registered client_id: {client_id}")

        # 2. Build authorize URL with PKCE
        verifier, challenge = generate_pkce()
        state = secrets.token_urlsafe(16)

        SCOPES = " ".join([
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/tasks.readonly",
            "https://www.googleapis.com/auth/contacts.readonly",
        ])

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": SCOPES,
        }
        auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

        # 3. Start callback server, open browser
        result = CallbackResult()
        runner = await run_callback_server(result)

        try:
            print(f"\nOpening browser for Google sign-in...")
            print(f"If it doesn't open, visit:\n{auth_url}\n")
            webbrowser.open(auth_url)

            print("Waiting for you to finish signing in (up to 3 minutes)...")
            try:
                await asyncio.wait_for(result.event.wait(), timeout=180)
            except asyncio.TimeoutError:
                print("ERROR: Timed out waiting for browser callback.", file=sys.stderr)
                sys.exit(1)

            if result.error:
                print(f"ERROR: Auth server returned error: {result.error}", file=sys.stderr)
                sys.exit(1)

            if result.state != state:
                print("ERROR: State mismatch - possible security issue, aborting.", file=sys.stderr)
                sys.exit(1)

            if not result.code:
                print("ERROR: No authorization code received.", file=sys.stderr)
                sys.exit(1)

            print("Got authorization code. Exchanging for token...")

            # 4. Exchange code for token
            token_resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": result.code,
                    "redirect_uri": REDIRECT_URI,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")

            if not access_token:
                print(f"ERROR: No access_token in response: {token_data}", file=sys.stderr)
                sys.exit(1)
            if not refresh_token:
                print(f"ERROR: No refresh_token in response - cannot set up auto-renewal: {token_data}", file=sys.stderr)
                sys.exit(1)

            print(f"\nGot access token (expires_in={token_data.get('expires_in')}s) and a refresh token.")
            push_secret_to_github(refresh_token, secret_name="MCP_REFRESH_TOKEN")
            push_secret_to_github(client_id, secret_name="MCP_CLIENT_ID")

        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
