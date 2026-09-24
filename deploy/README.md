# systemd files

Unit files for the Raspberry Pi deployment. Install them as `root` with the commands in
[the deployment guide](../docs/setup.md#systemd-paslauga):

```bash
sudo sh -c 'install -m 644 -t /etc/systemd/system /home/piagent/telegram-agent/deploy/piagent*.service /home/piagent/telegram-agent/deploy/piagent*.timer'
sudo systemctl daemon-reload
```

`install` copies the files. Don't link them instead: the `piagent` user owns the project folder,
so a link would let the bot rewrite its own service and drop its restrictions.

| File | Purpose |
|---|---|
| `piagent.service` | The bot, with systemd restrictions |
| `piagent-consolidate.service`, `.timer` | Nightly memory consolidation at 03:00 |
| `piagent-maintenance.service`, `.timer` | Weekly cleanup on Sundays at 03:15 |
| `piagent-backup.service`, `.timer` | Nightly encrypted backup to Google Drive at 03:30. Needs the script in `/usr/local/lib/piagent-backup/`, see [backups](../docs/setup.md#atsarginės-kopijos) |
| `piagent-unlock-alert.service` | Telegram alert while the encrypted data is locked. Enable it only with [data encryption](../docs/setup.md#10-įspėjimas-per-telegram-kai-diskas-užrakintas) |
