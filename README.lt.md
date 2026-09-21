# pi_agent

[English](README.md)

Asmeninis Telegram pagalbininkas, veikiantis Raspberry Pi. Jis atsako į klausimus, ieško
internete ir tvarko bendrą Google kalendorių nedidelei leidžiamų žmonių grupei. Su vartotojais
pagalbininkas kalba lietuviškai.

## Galimybės

- **Kalendorius**: rodo, kuria, keičia ir trina Google Calendar įvykius per
  [Composio](https://composio.dev). Kviečia tik žmones iš `users.toml`, įskaitant svečius, kurie
  botu nesinaudoja.
- **Paieška internete**: naudoja [Tavily](https://tavily.com) su ribotu rezultatų skaičiumi ir
  ištraukų ilgiu.
- **Patvirtinimai**: `update_event` ir `delete_event` visada reikalauja patvirtinimo mygtuko
  Telegram. Bet koks įrašymas po paieškos internete taip pat reikalauja patvirtinimo.
- **Atmintis**: paskutines žinutes laiko pažodžiui, senesnę istoriją sutraukia, išsaugo svarbius
  faktus apie kiekvieną vartotoją ir kas naktį juos konsoliduoja.
- **Išlaidų ribos**: skaičiuoja žetonus kiekvienam vartotojui ir taiko dienos ribas vartotojui
  bei visai sistemai.
- **Apsauga nuo raginimo injekcijų**: įrankių rezultatai, kalendoriaus įvykių tekstas, paieškos
  rezultatai ir išsaugoti faktai laikomi duomenimis, ne nurodymais.
- **Atsarginės kopijos**: nebūtinas skriptas šifruoja SQLite duomenų bazę su `gpg` ir įkelia ją į
  Google Drive, kopijas saugodamas 14 dienų.

## Architektūra

| Kelias | Paskirtis |
|---|---|
| `bot.py` | Paleidimo taškas |
| `agent/telegram_ui.py` | Telegram apdorojimas, patvirtinimo mygtukai ir žinučių skaidymas |
| `agent/loop.py` | Įrankius kviečiantis agento ciklas su iteracijų, laiko, žetonų ir kartojimų ribomis |
| `agent/llm.py` | [OpenRouter](https://openrouter.ai) klientas; numatytasis modelis `deepseek/deepseek-v4.1-flash` |
| `agent/tools/` | `search_web`, `list_events`, `create_event`, `update_event` ir `delete_event` |
| `agent/approvals.py` | Taisyklės, kuriems įrankių kvietimams reikia patvirtinimo |
| `agent/context.py`, `agent/memory.py` | Konteksto valdymas, santraukos ir faktai apie vartotojus |
| `agent/consolidate.py` | Naktinė atminties konsolidacija (paleidžia systemd laikmatis) |
| `agent/db.py` | SQLite saugykla, naudojimo apskaita ir ribos |
| `agent/journal.py` | JSONL žurnalas, iš kurio pašalinami API raktai ir el. pašto adresai |
| `backup/` | Savarankiškas šifruotų kopijų skriptas |
| `scripts/metrics.py` | Sėkmės, įrankių naudojimo ir išlaidų metrikos iš žurnalo |

## Reikalavimai

- Python 3.11 arba naujesnis
- Telegram, OpenRouter, Tavily ir Composio API raktai
- Google Calendar paskyra, prijungta per Composio

## Greita pradžia

Norėdami paleisti botą savo kompiuteryje:

```bash
git clone https://github.com/SimonasKairys/pi_agent.git
cd pi_agent
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env    # tada įrašykite savo raktus
```

Sukurkite `users.toml` failą su žmonėmis, kurie gali naudotis botu:

```toml
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "jusu@example.com"
timezone = "Europe/Vilnius"
role = "admin"
```

Paleiskite botą. Jis skaito `.env` iš darbinio katalogo, o `.env.example` duomenų kelius jau
nukreipia į projekto aplanką:

```bash
venv/bin/python bot.py
```

Saugiam diegimui į Raspberry Pi su systemd, atskiru vartotoju, naktine konsolidacija ir
atsarginėmis kopijomis žr. [diegimo gidą](docs/setup.md).

## Nustatymai

| Kintamasis | Būtinas | Aprašymas |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Taip | Boto tokenas iš [@BotFather](https://t.me/BotFather) |
| `OPENROUTER_API_KEY` | Taip | OpenRouter API raktas |
| `TAVILY_API_KEY` | Taip | Tavily API raktas paieškai |
| `COMPOSIO_API_KEY` | Taip | Composio API raktas su **Tool execution: Write** leidimu |
| `COMPOSIO_CONNECTED_ACCOUNT_ID` | Taip | Prijungtos Google Calendar paskyros ID (`ca_...`) |
| `PIAGENT_USERS_FILE` | Ne | Kelias iki `users.toml`. Numatytasis: `/etc/piagent/users.toml` |
| `PIAGENT_DB_PATH` | Ne | SQLite duomenų bazės kelias. Numatytasis: `/home/piagent/data/agent.db` |
| `PIAGENT_LOG_DIR` | Ne | JSONL žurnalo katalogas. Numatytasis: `/home/piagent/logs` |

Kopijavimo skriptas naudoja atskirus kintamuosius: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REFRESH_TOKEN`, `BACKUP_FOLDER_ID` ir `BACKUP_PASSPHRASE`.

## Testai

```bash
venv/bin/python -m pytest -q
```

Testai nekviečia išorinių API.

## Licencija

[MIT](LICENSE)
