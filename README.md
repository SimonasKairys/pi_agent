# pi_agent

[Lietuvių kalba](README.lt.md)

A personal Telegram assistant that runs on a Raspberry Pi. It answers questions, searches the
web, and manages a shared Google Calendar for a small, allowlisted group of people. The assistant
talks to users in Lithuanian.

## Features

- **Calendar**: lists, creates, updates, and deletes Google Calendar events through
  [Composio](https://composio.dev). Invite only people listed in `users.toml`, including guests
  who don't use the bot.
- **Web search**: uses [Tavily](https://tavily.com) with capped result counts and snippet lengths.
- **Approvals**: `update_event`, `delete_event`, and `forget_fact` always require a confirmation button in
  Telegram. Any write after a web search also requires confirmation.
- **Memory**: keeps recent messages verbatim, summarizes older history, stores important facts
  about each user, and consolidates them nightly. Users can ask what the bot remembers and ask
  it to forget a fact (`list_facts`, `forget_fact`).
- **Cost limits**: tracks token usage per user and enforces daily per-user and system-wide
  spending caps.
- **Prompt-injection defense**: tool results, calendar event text, search results, and stored
  facts are wrapped and treated as data, not instructions.
- **Backups**: an optional script encrypts the SQLite database with `gpg` and uploads it to
  Google Drive with 14-day retention.

## Architecture

| Path | Purpose |
|---|---|
| `bot.py` | Entry point |
| `agent/telegram_ui.py` | Telegram handlers, approval buttons, and message splitting |
| `agent/loop.py` | Tool-calling agent loop with iteration, time, token, and retry limits |
| `agent/llm.py` | [OpenRouter](https://openrouter.ai) client; default model `deepseek/deepseek-v4.1-flash` |
| `agent/tools/` | `search_web`, `list_events`, `create_event`, `update_event`, `delete_event`, `list_facts`, and `forget_fact` |
| `agent/approvals.py` | Rules for which tool calls need user confirmation |
| `agent/context.py`, `agent/memory.py` | Context window management, summaries, and user facts |
| `agent/consolidate.py` | Nightly memory consolidation (runs from a systemd timer) |
| `agent/db.py` | SQLite storage, usage tracking, and limits |
| `agent/journal.py` | JSONL run log with API keys and email addresses scrubbed |
| `backup/` | Standalone encrypted backup script |
| `scripts/metrics.py` | Success rate, tool usage, and cost metrics from the run log |

## Requirements

- Python 3.11 or later
- API keys for Telegram, OpenRouter, Tavily, and Composio
- A Google Calendar account connected through Composio

## Quick start

To run the bot locally:

```bash
git clone https://github.com/SimonasKairys/pi_agent.git
cd pi_agent
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env    # then fill in your keys
```

Create a `users.toml` file that lists who can use the bot:

```toml
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "you@example.com"
timezone = "Europe/Vilnius"
role = "admin"
```

Start the bot. It reads `.env` from the working directory, and `.env.example` already points the
data paths at the project folder:

```bash
venv/bin/python bot.py
```

For a hardened Raspberry Pi deployment with systemd, a dedicated user, nightly consolidation, and
backups, see the [deployment guide](docs/setup.md) (in Lithuanian).

To let an AI assistant guide the installation, give it the
[step-by-step LLM install instructions](docs/llm-install.md). They are written for small models
(3B parameters and up), and the assistant first asks which language you want to use.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |
| `TAVILY_API_KEY` | Yes | Tavily API key for web search |
| `COMPOSIO_API_KEY` | Yes | Composio API key with **Tool execution: Write** permission |
| `COMPOSIO_CONNECTED_ACCOUNT_ID` | Yes | Connected Google Calendar account ID (`ca_...`) |
| `PIAGENT_USERS_FILE` | No | Path to `users.toml`. Default: `/etc/piagent/users.toml` |
| `PIAGENT_DB_PATH` | No | SQLite database path. Default: `/home/piagent/data/agent.db` |
| `PIAGENT_LOG_DIR` | No | JSONL log directory. Default: `/home/piagent/logs` |

The backup script uses its own variables: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REFRESH_TOKEN`, `BACKUP_FOLDER_ID`, and `BACKUP_PASSPHRASE`.

## Tests

```bash
venv/bin/python -m pytest -q
```

The tests don't call external APIs.

## License

[MIT](LICENSE)
