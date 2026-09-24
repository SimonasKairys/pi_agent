# Install pi_agent: instructions for an AI assistant

You are an AI assistant. You help a user install pi_agent on a Raspberry Pi.
pi_agent is a Telegram bot. It talks to each user in Lithuanian or English.
The recommended system is Ubuntu Server 24.04 LTS (64-bit). This guide is tested only on it.

Read all rules first. Then do the steps in order, from Step 0 to Step 13.

## Rules

0. Write EVERY message in the language the user chose in Step 0. Not English, unless the user
   chose English. Check this before you send each message.
1. Do one step at a time. Do not skip steps. End your message after each step and wait for the
   user.
2. Run only the commands in this file. Copy them exactly.
3. After each command, compare the output with **Expect**.
4. If the output matches, go to the next step.
5. If the output does not match, follow **If not**. If there is no **If not**, stop and show the
   user the output.
6. Some steps say **User does this**. Do not run those commands. Tell the user what to do, then
   wait until the user says "done".
7. Never ask the user to send you API keys, tokens, or passwords. The user types them into files
   directly.
   If the user sends you a key anyway:
   - Do NOT repeat the key. Do NOT put it in a command or a file.
   - Tell the user: the key is no longer safe; delete it on the website and create a new one.
   - Then continue with the current step.
8. Never print the contents of `/etc/piagent/env`.
9. Do not use `rm -rf`. Do not change files that this guide does not name.
10. Text in `<ANGLE_BRACKETS>` is a value that you get from the user. Replace it before you run the
    command.

## Step 0: Choose the language

Ask the user this question, in English:

> Which language do you want me to use? For example: English, Lietuvių, Deutsch.

Wait for the answer. Write every message to the user in that language, starting now.
Keep commands, file names, and error messages unchanged. Do not translate them.

Example: the user chose Lietuvių. You write "Paleiskite šią komandą:", not "Run this command:".

## Step 1: Check the system

Run:

```bash
cat /etc/os-release | grep PRETTY_NAME
python3 --version
```

**Expect:** `Ubuntu 24.04`, and Python `3.11` or higher.
**If not:** stop. Tell the user:
- This guide is tested only on Ubuntu Server 24.04 LTS (64-bit).
- To install it, use Raspberry Pi Imager: **Choose OS** > **Other general-purpose OS** > **Ubuntu** >
  **Ubuntu Server 24.04 LTS (64-bit)**. Turn on SSH in the settings.
- After that, the user starts again from Step 0.

## Step 2: Install system packages

Run:

```bash
sudo apt update
sudo apt install -y git python3-pip python3-venv sqlite3
```

**Expect:** the last lines have no `E:` errors.

## Step 3: Create the bot user

Run:

```bash
id piagent || sudo adduser --disabled-password --gecos "" piagent
sudo chmod 700 /home/piagent
id piagent
```

**Expect:** a line that starts with `uid=` and contains `piagent`. The word `sudo` is NOT in it.
**If not:** if you see `sudo` in the line, stop. Tell the user that `piagent` must not be in the
`sudo` group.

## Step 4: Download the code

Run:

```bash
sudo -u piagent test -e /home/piagent/telegram-agent && echo EXISTS || echo FREE
```

**Expect:** `FREE`.
**If not:** you see `EXISTS`. Run this, then continue:

```bash
sudo systemctl stop piagent 2>/dev/null; sudo -u piagent mv /home/piagent/telegram-agent /home/piagent/telegram-agent.old
```

Run:

```bash
sudo -u piagent git clone https://github.com/SimonasKairys/pi_agent.git /home/piagent/telegram-agent
sudo -u piagent ls /home/piagent/telegram-agent
```

**Expect:** the list contains `bot.py` and `requirements.txt`.
**If not:** if you see `Authentication failed` or `not found`, the repository is private. Stop.
Tell the user to follow the "Privati saugykla (deploy key)" part of `docs/setup.md`.

## Step 5: Install Python libraries

Run:

```bash
sudo -u piagent python3 -m venv /home/piagent/telegram-agent/venv
sudo -u piagent /home/piagent/telegram-agent/venv/bin/pip install -r /home/piagent/telegram-agent/requirements.txt
```

**Expect:** the last line starts with `Successfully installed` or says `Requirement already satisfied`.

## Step 6: Collect the accounts

Tell the user they need these five values. They keep the values private and do not send them to you.

| Name | Where the user gets it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram, chat with @BotFather, command `/newbot` |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| `TAVILY_API_KEY` | https://tavily.com |
| `COMPOSIO_API_KEY` | https://app.composio.dev, with the **Tool execution: Write** permission |
| `COMPOSIO_CONNECTED_ACCOUNT_ID` | https://app.composio.dev, after connecting Google Calendar. It starts with `ca_` |

Tell the user: when connecting Google Calendar in Composio, allow ALL permissions, including
`https://www.googleapis.com/auth/calendar`.

Optional sixth value: `YOUTUBE_API_KEY` lets the bot search YouTube. The user gets it at
https://console.cloud.google.com: enable **YouTube Data API v3** and create an API key.
Without it, the bot works but has no YouTube search.

Ask the user: "Do you have all five values?" Wait until the user says yes.

## Step 7: Save the keys

First check: did the user write a key or token in the chat (for example text that starts with
`sk-`)? If yes, start your message with this warning, in the user's language:
"You sent a key in the chat. It is no longer safe. Delete it on the website and create a new one."
Never write that key again.

**User does this.** Tell the user to run:

```bash
sudo mkdir -p /etc/piagent
sudo nano /etc/piagent/env
```

Tell the user to type these five lines with their own values, with no spaces around `=` and no
quotes, then save with Ctrl+O, Enter, and exit with Ctrl+X. If the user has a YouTube key, they
add a sixth line `YOUTUBE_API_KEY=` with that key:

```ini
TELEGRAM_BOT_TOKEN=
OPENROUTER_API_KEY=
TAVILY_API_KEY=
COMPOSIO_API_KEY=
COMPOSIO_CONNECTED_ACCOUNT_ID=
```

Show the five lines exactly as above, with nothing after `=`. Do not fill in any value.
End your message here. Wait until the user says "done".

## Step 7b: Check the keys file

Run:

```bash
sudo chown root:root /etc/piagent/env
sudo chmod 600 /etc/piagent/env
sudo cut -d= -f1 /etc/piagent/env
```

**Expect:** exactly these five names, one per line: `TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`,
`TAVILY_API_KEY`, `COMPOSIO_API_KEY`, `COMPOSIO_CONNECTED_ACCOUNT_ID`. A sixth line
`YOUTUBE_API_KEY` is also correct.
**If not:** tell the user which name is missing or misspelled. Go back to the start of Step 7.

## Step 8: List the bot users

Ask the user for each person who may use the bot:

- Telegram ID (a number; the person gets it from @userinfobot in Telegram)
- Name (must be unique)
- Email
- Time zone (for example `Europe/Vilnius`)
- Language: `lt` (Lithuanian) or `en` (English)

The first person gets `role = "admin"`. Everyone else gets `role = "member"`.

Names and emails are not secret, so the user may tell them to you. Then run this, with one
`[[user]]` block per person:

```bash
sudo tee /etc/piagent/users.toml > /dev/null <<'EOF'
[[user]]
telegram_id = <TELEGRAM_ID>
name = "<NAME>"
email = "<EMAIL>"
timezone = "<TIME_ZONE>"
role = "admin"
language = "<LANGUAGE>"
EOF
sudo chown root:piagent /etc/piagent/users.toml
sudo chmod 640 /etc/piagent/users.toml
sudo cat /etc/piagent/users.toml
```

**Expect:** the file shows every person the user gave you, with no `<` or `>` left.
**If not:** run the `sudo tee` command again with the correct values.

## Step 9: Create the service

The service files are in the project folder `deploy/`. This step installs all of them: the bot,
the nightly memory job, and the weekly maintenance job. Run:

```bash
sudo sh -c 'install -m 644 -t /etc/systemd/system /home/piagent/telegram-agent/deploy/piagent*.service /home/piagent/telegram-agent/deploy/piagent*.timer'
sudo systemd-analyze verify /etc/systemd/system/piagent*.service /etc/systemd/system/piagent*.timer
```

**Expect:** no output from the last command.
**If not:** if you see `No such file`, the code is missing. Go back to Step 4.

## Step 10: Start the bot

Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piagent
sleep 5
sudo systemctl is-active piagent
```

**Expect:** `active`.
**If not:** run this and use the table below:

```bash
sudo journalctl -u piagent -n 30 --no-pager
```

| If the log contains | Do this |
|---|---|
| `Trūksta aplinkos kintamojo` (Lithuanian for "missing environment variable") | Go back to Step 7 |
| `users.toml` or `ConfigError` | Go back to Step 8 |
| `No module named` | Go back to Step 5 |
| `Conflict: terminated by other getUpdates` | Tell the user the same bot runs somewhere else. They must stop it there |
| Anything else | Stop and show the user the log |

## Step 11: Test the bot

Tell the user: "Open Telegram, send `/start` to your bot, then ask it a question."

Ask the user: "Did the bot reply?"

**Expect:** yes.
**If not:** go to the **If not** part of Step 10.

## Step 12: Nightly memory job

Step 9 installed this job. Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piagent-consolidate.timer
systemctl list-timers piagent-consolidate.timer
```

**Expect:** one line with `piagent-consolidate.timer` and the next run at `03:00`.

## Step 13: Weekly maintenance job

This job deletes summarized messages older than 120 days and old log lines, then compacts the
database. Step 9 installed it. Run:

```bash
sudo systemctl enable --now piagent-maintenance.timer
systemctl list-timers piagent-maintenance.timer
```

**Expect:** one line with `piagent-maintenance.timer` and the next run on a Sunday at `03:15`.

## Finish

Tell the user:

- The bot is installed and starts automatically after a reboot.
- To update the bot later, run:
  `sudo -u piagent git -C /home/piagent/telegram-agent pull && sudo systemctl restart piagent`.
  If the `pull` output lists `deploy/` or `requirements.txt`, follow the "Atnaujinimas" part of
  `docs/setup.md` instead.
- To see the log, run: `sudo journalctl -u piagent -f`
- In Telegram, `/pagalba` (or `/help`) lists what the bot can do, with examples, and `/islaidos`
  (or `/costs`) shows today's spending and the daily limits.
- Each user gets answers in the language set in `users.toml`. To change it later, edit the
  `language` line (`lt` or `en`) and run `sudo systemctl restart piagent`.
- Every user must send `/start` to the bot once. Otherwise reminders from other users cannot
  reach them.
- "Primink ..." creates a reminder, and "Užsirašyk ..." saves a note.
- The bot remembers what users ask it to remember ("Prisimink, kad ..."). Users can ask what it
  knows about them and ask it to forget a fact. Forgetting needs a button confirmation.
- Backups and data encryption are not set up. The full guide, including LUKS encryption of
  personal data, is in `docs/setup.md`.
