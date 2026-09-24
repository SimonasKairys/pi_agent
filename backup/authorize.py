"""Vienkartinis GOOGLE_REFRESH_TOKEN gavimas kopijavimo skriptui backup.py.

Kompiuteryje su naršykle:

    python backup/authorize.py                       # paklausia kliento ID ir paslapties
    python backup/authorize.py client_secret.json    # arba naudoja kliento JSON failą

Raspberry Pi be naršyklės: adresą atidarote kitame įrenginyje, o naršyklės grąžintą adresą
įklijuojate atgal. Raktai įrašomi tiesiai į backup.env:

    sudo /usr/local/lib/piagent-backup/venv/bin/python \\
        /home/piagent/telegram-agent/backup/authorize.py --headless --env-file /etc/piagent/backup.env

Klientas turi būti Desktop tipo (Google Auth Platform > Clients). Be --env-file skriptas išveda
tris eilutes, kurias reikia įrašyti į /etc/piagent/backup.env. Tokenas yra slaptas: jo
nesaugokite kituose failuose.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any, Callable

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
# Desktop clients accept any localhost port; nothing listens there in --headless mode
HEADLESS_REDIRECT_URI = "http://localhost:8765/"
ENV_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")


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


def run_headless(flow: Any, read: Callable[[str], str] = input) -> Any:
    """Consent flow without a local browser: the user pastes the redirect address back."""
    flow.redirect_uri = HEADLESS_REDIRECT_URI
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    print("\n1. Atidarykite šį adresą naršyklėje bet kuriame įrenginyje:\n")
    print(url)
    print("\n2. Prisijunkite ir leiskite prieigą. Naršyklė parodys klaidą, kad localhost")
    print("   nepasiekiamas. Tai normalu.")
    print("3. Nukopijuokite visą adresą iš adreso juostos (prasideda http://localhost:8765/).\n")
    response = read("Įklijuokite adresą: ").strip()
    if "code=" not in response:
        raise RuntimeError("Adrese nėra code=. Nukopijuokite visą adresą iš adreso juostos.")
    # oauthlib accepts only https responses; the localhost redirect is http by design
    flow.fetch_token(authorization_response=response.replace("http://", "https://", 1))
    return flow.credentials


def authorize(
    client_secrets_file: str | None = None,
    config: dict[str, Any] | None = None,
    flow_class: Any = None,
    headless: bool = False,
    read: Callable[[str], str] = input,
) -> dict[str, str]:
    """Runs the consent flow and returns the backup.env credentials."""
    if flow_class is None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow_class = InstalledAppFlow

    if client_secrets_file:
        flow = flow_class.from_client_secrets_file(client_secrets_file, SCOPES)
    else:
        flow = flow_class.from_client_config(config, SCOPES)
    if headless:
        creds = run_headless(flow, read)
    else:
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


def read_env_value(path: Path, key: str) -> str:
    """Returns KEY's value from an env file, or an empty string."""
    for line in path.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip()
    return ""


def update_env_file(path: Path, values: dict[str, str]) -> None:
    """Replaces or appends KEY=value lines and keeps every other line as it was.

    The file is rewritten atomically with mode 600, because it holds secrets.
    """
    remaining = dict(values)
    lines = []
    for line in path.read_text().splitlines():
        name = line.partition("=")[0].strip()
        lines.append(f"{name}={remaining.pop(name)}" if name in remaining else line)
    lines.extend(f"{key}={value}" for key, value in remaining.items())

    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Gauna GOOGLE_REFRESH_TOKEN kopijavimui.")
    parser.add_argument("client_secrets_file", nargs="?", help="Desktop kliento JSON failas")
    parser.add_argument("--headless", action="store_true",
                        help="be naršyklės: adresą atidarote kitame įrenginyje")
    parser.add_argument("--env-file", type=Path,
                        help="įrašo raktus į šį failą, pvz. /etc/piagent/backup.env")
    args = parser.parse_args(argv[1:])

    if args.env_file and not args.env_file.is_file():
        print(f"Klaida: failas {args.env_file} nerastas", file=sys.stderr)
        return 2

    if args.client_secrets_file:
        values = authorize(args.client_secrets_file, headless=args.headless)
    else:
        current_id = read_env_value(args.env_file, "GOOGLE_CLIENT_ID") if args.env_file else ""
        hint = f" [Enter – palikti {current_id}]" if current_id else ""
        client_id = input(f"GOOGLE_CLIENT_ID{hint}: ").strip() or current_id
        # getpass: the secret is not echoed and does not reach the shell history
        client_secret = getpass.getpass("GOOGLE_CLIENT_SECRET (nerodomas): ").strip()
        if not client_id or not client_secret:
            print("Klaida: reikia ir kliento ID, ir paslapties", file=sys.stderr)
            return 2
        values = authorize(config=client_config(client_id, client_secret), headless=args.headless)

    if args.env_file:
        update_env_file(args.env_file, values)
        print(f"\nRaktai įrašyti į {args.env_file}: {', '.join(ENV_KEYS)}.")
        return 0
    print("\nĮrašykite šias eilutes į /etc/piagent/backup.env vietoj senų:\n")
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
