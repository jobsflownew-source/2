#!/usr/bin/env python3
"""Script interactivo para obtener TikTok refresh_token.

Pre-requisitos en TikTok Developer Portal:
1. Cuenta en https://developers.tiktok.com
2. App creada (Sandbox o Production)
3. "Login Kit" activado
4. "Content Posting API" activado con scopes:
       video.upload    (para Direct Inbox - sin audit)
       video.publish   (para Direct Post - requiere audit)
5. Redirect URI registrada: http://localhost:8094/callback
6. Tu cuenta TikTok anadida como "tester" en Sandbox

Uso:
    python3 scripts/tiktok_oauth.py \\
        --client-key TU_CLIENT_KEY \\
        --client-secret TU_CLIENT_SECRET

Abre un navegador para autenticar. Tras autorizar, imprime el
refresh_token que pegas en .env como TIKTOK_REFRESH_TOKEN.

No requiere librerias externas (solo http.server + httpx + secrets).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser
from typing import Optional

try:
    import httpx
except ImportError:
    print("\nERROR: falta httpx")
    print("Instala: pip3 install --user httpx\n")
    sys.exit(1)


AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

DEFAULT_PORT = 8094
DEFAULT_REDIRECT = f"http://localhost:{DEFAULT_PORT}/callback"
SCOPES = "user.info.basic,video.upload"   # cambia a video.publish si tu app fue auditada


# Compartido entre el http server y el main thread
_received: dict = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404); self.end_headers(); return
        params = urllib.parse.parse_qs(parsed.query)
        _received.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><html><head><title>TikTok OAuth OK</title></head>"
            b"<body style='font-family:sans-serif;text-align:center;margin-top:80px'>"
            b"<h2 style='color:#2ecc71'>OAuth completed</h2>"
            b"<p>Puedes cerrar esta pestana y volver a la terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, *args, **kwargs):
        return  # silencia los logs de http.server


def _build_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def _start_callback_server(port: int) -> http.server.HTTPServer:
    server = http.server.HTTPServer(("0.0.0.0", port), CallbackHandler)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server


def _exchange_code_for_tokens(
    code: str, client_key: str, client_secret: str,
    redirect_uri: str, code_verifier: str,
) -> dict:
    payload = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
    }
    r = httpx.post(TOKEN_URL, data=payload, headers=headers, timeout=30)
    if r.status_code >= 400:
        print(f"\nERROR HTTP {r.status_code}: {r.text}")
        r.raise_for_status()
    return r.json()


def main() -> int:
    p = argparse.ArgumentParser(
        description="Obtiene TikTok refresh_token para subida automatica.",
    )
    p.add_argument("--client-key", required=True, help="TikTok app Client Key")
    p.add_argument("--client-secret", required=True,
                   help="TikTok app Client Secret")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"Puerto local para el callback (default: {DEFAULT_PORT})")
    p.add_argument("--scopes", default=SCOPES,
                   help=f"Scopes OAuth separados por coma (default: {SCOPES})")
    args = p.parse_args()

    redirect_uri = f"http://localhost:{args.port}/callback"
    state = secrets.token_urlsafe(16)
    verifier, challenge = _build_pkce()

    auth_qs = urllib.parse.urlencode({
        "client_key": args.client_key,
        "scope": args.scopes,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTH_URL}?{auth_qs}"

    print(">>> Iniciando flujo OAuth TikTok (PKCE).")
    print(f">>> Servidor local: http://localhost:{args.port}")
    print(">>> IMPORTANTE: en TikTok Developer Portal -> Login Kit ->")
    print(f"    asegurate de tener registrada exactamente esta Redirect URI:")
    print(f"      {redirect_uri}")
    print()
    print("Si tu navegador no se abre, copia y pega esta URL:\n")
    print(auth_url)
    print()

    server = _start_callback_server(args.port)
    try:
        try:
            webbrowser.open(auth_url, new=2)
        except Exception:
            pass

        # Espera el callback (max 5 min)
        deadline = time.time() + 300
        while time.time() < deadline and "code" not in _received:
            time.sleep(0.5)

        if "code" not in _received:
            print("\nERROR: No se recibio el code dentro del timeout.")
            return 1

        if _received.get("state") != state:
            print(f"\nERROR: state mismatch. Esperado={state}"
                  f" recibido={_received.get('state')}")
            return 1

        code = _received["code"]
        print(f">>> Code recibido. Intercambiando por tokens...")
        data = _exchange_code_for_tokens(
            code, args.client_key, args.client_secret,
            redirect_uri, verifier,
        )

        if "refresh_token" not in data:
            print(f"\nERROR: TikTok no devolvio refresh_token: {data}")
            return 1

        print("\n" + "=" * 60)
        print("EXITO. Pega esto en tu archivo .env:")
        print("=" * 60)
        print(f"TIKTOK_CLIENT_KEY={args.client_key}")
        print(f"TIKTOK_CLIENT_SECRET={args.client_secret}")
        print(f"TIKTOK_REFRESH_TOKEN={data['refresh_token']}")
        if data.get("open_id"):
            print(f"TIKTOK_OPEN_ID={data['open_id']}")
        print("=" * 60)
        print()
        print("Token info:")
        print(f"  scopes      : {data.get('scope')}")
        print(f"  expires_in  : {data.get('expires_in')} seg")
        print(f"  refresh_in  : {data.get('refresh_expires_in')} seg")
        print()
        print("Despues:")
        print("  docker compose up -d --force-recreate render-service")
        print()
        return 0

    finally:
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
