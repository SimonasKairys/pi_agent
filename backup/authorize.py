"""Vienkartinis GOOGLE_REFRESH_TOKEN gavimas kopijavimo skriptui backup.py.

Paleidžiamas kompiuteryje su naršykle, ne Raspberry Pi:

    python backup/authorize.py                       # paklausia kliento ID ir paslapties
    python backup/authorize.py client_secret.json    # arba naudoja kliento JSON failą

Klientas turi būti Desktop tipo (Google Auth Platform > Clients). Skriptas atidaro Google
prisijungimą naršyklėje ir išveda tris eilutes, kurias reikia įrašyti į /etc/piagent/backup.env. Tokenas yra slaptas: jo nesaugokite kituose failuose.
"""

from __future__ import annotations

import getpass
import sys
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def client_config(client_id: str, client_secret: str) -> dict[str, Any]:
    """Builds the Desktop client config that a downloaded client_secret.json would contain."""
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def authorize(
    client_secrets_file: str | None = None,
    config: dict[str, Any] | None = None,
    flow_class: Any = None,
) -> dict[str, str]:
    """Runs the browser consent flow and returns the backup.env credentials."""
    if flow_class is None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow_class = InstalledAppFlow

    if client_secrets_file:
        flow = flow_class.from_client_secrets_file(client_secrets_file, SCOPES)
    else:
        flow = flow_class.from_client_config(config, SCOPES)
    # prompt=consent makes Google return a refresh token even if access was granted before
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise RuntimeError("Google negrąžino refresh token. Pašalinkite programos prieigą "
                           "https://myaccount.google.com/permissions ir bandykite iš naujo.")
    return {
        "GOOGLE_CLIENT_ID": creds.client_id,
        "GOOGLE_CLIENT_SECRET": creds.client_secret,
        "GOOGLE_REFRESH_TOKEN": creds.refresh_token,
    }


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("Naudojimas: python backup/authorize.py [client_secret.json]", file=sys.stderr)
        return 2
    if len(argv) == 2:
        values = authorize(argv[1])
    else:
        client_id = input("GOOGLE_CLIENT_ID: ").strip()
        # getpass: the secret is not echoed and does not reach the shell history
        client_secret = getpass.getpass("GOOGLE_CLIENT_SECRET (nerodomas): ").strip()
        if not client_id or not client_secret:
            print("Klaida: reikia ir kliento ID, ir paslapties", file=sys.stderr)
            return 2
        values = authorize(config=client_config(client_id, client_secret))
    print("\nĮrašykite šias eilutes į /etc/piagent/backup.env vietoj senų:\n")
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
