#!/usr/bin/env python3
"""Script interactivo para obtener YouTube refresh_token.

Pre-requisitos en Google Cloud Console:
1. Proyecto creado
2. YouTube Data API v3 habilitada
3. Pantalla de consentimiento OAuth configurada (External + Testing)
4. Tu email Google anadido como "Test user"
5. Credencial OAuth 2.0 Client ID tipo "Desktop app" creada
   - Te dara client_id y client_secret

Uso:
    python3 scripts/youtube_oauth.py \\
        --client-id TU_CLIENT_ID.apps.googleusercontent.com \\
        --client-secret TU_CLIENT_SECRET

Abrira un navegador para autenticar. Tras autorizar, imprime el
refresh_token que debes pegar en el .env como YOUTUBE_REFRESH_TOKEN.

Requiere:
    pip install google-auth-oauthlib
"""
from __future__ import annotations

import argparse
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("\nERROR: falta google-auth-oauthlib")
    print("Instala con: pip install google-auth-oauthlib")
    print("O en WSL Ubuntu: sudo apt install python3-pip && pip3 install --user google-auth-oauthlib\n")
    sys.exit(1)


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Obtiene YouTube refresh_token para subida automatica.",
    )
    p.add_argument("--client-id", required=True, help="OAuth Client ID")
    p.add_argument("--client-secret", required=True, help="OAuth Client Secret")
    p.add_argument(
        "--port", type=int, default=8090,
        help="Puerto local para el callback OAuth (default: 8090)",
    )
    args = p.parse_args()

    config = {
        "installed": {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    print(">>> Iniciando flujo OAuth...")
    flow = InstalledAppFlow.from_client_config(config, SCOPES)

    print(f">>> Servidor local en http://localhost:{args.port}")
    print(">>> Si tu navegador no se abre solo, copia y pega la URL que aparezca.")
    print(">>> IMPORTANTE: en la pantalla de Google selecciona el canal de YouTube"
          " donde quieres subir los shorts.\n")

    creds = flow.run_local_server(
        port=args.port,
        open_browser=True,
        prompt="consent",          # fuerza obtener refresh_token cada vez
        access_type="offline",     # imprescindible para refresh_token
    )

    if not creds.refresh_token:
        print("\nERROR: No se obtuvo refresh_token.")
        print("Revoca el acceso en https://myaccount.google.com/permissions")
        print("y vuelve a ejecutar este script.\n")
        return 1

    print("\n" + "=" * 60)
    print("EXITO. Pega esto en tu archivo .env:")
    print("=" * 60)
    print(f"YOUTUBE_CLIENT_ID={args.client_id}")
    print(f"YOUTUBE_CLIENT_SECRET={args.client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)
    print("\nDespues:")
    print("  docker compose up -d --force-recreate render-service")
    print("\nY verifica:")
    print("  curl http://localhost:8000/health")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
