# pi_agent

[English](README.md)

Asmeninis Telegram pagalbininkas, veikiantis Raspberry Pi. Jis atsako į klausimus, ieško
internete ir tvarko bendrą Google kalendorių nedidelei leidžiamų žmonių grupei. Su kiekvienu
vartotoju pagalbininkas kalba lietuviškai (numatytoji kalba) arba angliškai, pagal `users.toml`.

## Galimybės

- **Kalendorius**: rodo, kuria, keičia ir trina Google Calendar įvykius per
  [Composio](https://composio.dev). Kviečia tik žmones iš `users.toml`, įskaitant svečius, kurie
  botu nesinaudoja.
- **Paieška internete**: naudoja [Tavily](https://tavily.com) su ribotu rezultatų skaičiumi ir
  ištraukų ilgiu.
- **Paieška YouTube** (nebūtina): randa vaizdo įrašus per
  [YouTube Data API](https://developers.google.com/youtube/v3) ir grąžina pavadinimus, kanalus, datas
  ir nuorodas. Įrankis veikia tik nustačius `YOUTUBE_API_KEY`.
- **Patvirtinimai**: `update_event`, `delete_event`, `forget_fact`, `delete_note`, `delete_reminder` ir
  priminimai kitiems vartotojams visada reikalauja patvirtinimo mygtuko
  Telegram. Bet koks įrašymas po paieškos internete ar YouTube taip pat reikalauja patvirtinimo.
- **Atmintis**: paskutines žinutes laiko pažodžiui, senesnę istoriją sutraukia, išsaugo svarbius
  faktus apie kiekvieną vartotoją ir kas naktį juos konsoliduoja. Vartotojas gali paklausti, ką
  botas apie jį atsimena, ir paprašyti pamiršti faktą (`list_facts`, `forget_fact`).
- **Priminimai**: „Primink rytoj 9 val. ...“ sukuria vienkartinį priminimą Telegram sau arba kitam
  boto vartotojui (su patvirtinimo mygtuku). Priminimai išlieka perkrovus botą, o priminimas, kurio
  laikas atėjo botui neveikiant, atsiunčiamas vėliau su pastaba.
- **Užrašai**: „Užsirašyk ...“ išsaugo idėją ar mintį. Užrašų ieškoma tik paklausus, jie nesiunčiami
  su kiekviena užklausa, todėl išlaidų nedidina.
- **Kalbos**: kiekvienam vartotojui lietuvių (numatytoji) arba anglų kalba, nustatoma `users.toml`
  lauku `language`: atsakymai, pagalba, patvirtinimo kortelės, priminimai ir komandų meniu.
- **Išlaidų ribos**: įrašo tikrąją kiekvieno modelio kvietimo kainą, kurią grąžina OpenRouter, ir
  taiko dienos ribas vartotojui bei visai sistemai. Komanda `/islaidos` parodo šiandienos išlaidas,
  o administratorius mato ir kiekvieno vartotojo išlaidas.
- **Apsauga nuo raginimo injekcijų**: įrankių rezultatai, kalendoriaus įvykių tekstas, paieškos
  rezultatai ir išsaugoti faktai laikomi duomenimis, ne nurodymais.
- **Duomenų šifravimas**: nebūtina sąranka duomenų bazę, žurnalą ir `users.toml` laiko LUKS2
  konteineryje, kurį po kiekvieno perkrovimo atrakinate slaptažodžiu.
- **Atsarginės kopijos**: nebūtinas skriptas šifruoja SQLite duomenų bazę su `gpg` ir įkelia ją į
  Google Drive, kopijas saugodamas 14 dienų.

## Telegram komandos

| Komanda | Ką daro |
|---|---|
| `/start` | Pasisveikina |
| `/pagalba` arba `/help` | Parodo, ką botas moka, su užklausų pavyzdžiais |
| `/islaidos` arba `/costs` | Parodo šiandienos išlaidas ir dienos ribas |

Abu pavadinimai veikia visiems. Komandų meniu (mygtukas **/**) rodo pavadinimus vartotojo kalba.

Visa kita rašoma laisvu tekstu: botas supranta tokius prašymus kaip „Primink rytoj 9 val. ...“.

## Architektūra

| Kelias | Paskirtis |
|---|---|
| `bot.py` | Paleidimo taškas |
| `agent/telegram_ui.py` | Telegram apdorojimas, patvirtinimo mygtukai ir žinučių skaidymas |
| `agent/loop.py` | Įrankius kviečiantis agento ciklas su iteracijų, laiko, žetonų ir kartojimų ribomis |
| `agent/llm.py` | [OpenRouter](https://openrouter.ai) klientas; numatytasis modelis `deepseek/deepseek-v4.1-flash`, nukreipiamas į greičiausią tiekėją neviršijant kainos ribos |
| `agent/tools/` | Paieškos internete ir YouTube, kalendoriaus, faktų, užrašų ir priminimų įrankiai |
| `agent/reminders.py` | Fono ciklas, kuris kas 30 s išsiunčia atėjusius priminimus |
| `agent/i18n.py` | Vartotojui rodomi tekstai lietuvių ir anglų kalbomis |
| `agent/approvals.py` | Taisyklės, kuriems įrankių kvietimams reikia patvirtinimo |
| `agent/context.py`, `agent/memory.py` | Konteksto valdymas, santraukos ir faktai apie vartotojus |
| `agent/consolidate.py` | Naktinė atminties konsolidacija (paleidžia systemd laikmatis) |
| `agent/maintenance.py` | Savaitinė priežiūra: ištrina sutrauktas senesnes nei 120 dienų žinutes, užbaigtus patvirtinimus ir priminimus bei senas žurnalo eilutes, tada suspaudžia duomenų bazę |
| `agent/db.py` | SQLite saugykla, naudojimo apskaita ir ribos |
| `agent/journal.py` | JSONL žurnalas, iš kurio pašalinami API raktai ir el. pašto adresai |
| `backup/` | Savarankiškas šifruotų kopijų skriptas |
| `deploy/` | systemd paslaugų ir laikmačių failai Raspberry Pi |
| `scripts/metrics.py` | Sėkmės, įrankių naudojimo ir išlaidų metrikos iš žurnalo |

## Reikalavimai

- Rekomenduojama: [Ubuntu Server 24.04 LTS](https://ubuntu.com/download/raspberry-pi) (64 bitų)
  Raspberry Pi 5. Diegimo gidas ir LLM diegimo instrukcija parašyti ir išbandyti būtent jai: joje yra
  Python 3.12, nėra darbalaukio, kuris naudotų atmintį, o saugumo atnaujinimai teikiami iki 2029 m.
  Į SD kortelę ją įrašysite su [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
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
language = "lt"    # nebūtina: "lt" (numatytoji) arba "en"
```

Paleiskite botą. Jis skaito `.env` iš darbinio katalogo, o `.env.example` duomenų kelius jau
nukreipia į projekto aplanką:

```bash
venv/bin/python bot.py
```

Saugiam diegimui į Raspberry Pi su systemd, atskiru vartotoju, naktine konsolidacija ir
atsarginėmis kopijomis žr. [diegimo gidą](docs/setup.md).

Jei norite, kad diegimą vestų AI asistentas, pateikite jam
[žingsnis po žingsnio instrukciją LLM modeliui](docs/llm-install.md). Ji parašyta mažiems modeliams
(nuo 3B parametrų), o asistentas pirmiausia paklausia, kuria kalba norite bendrauti.

## Nustatymai

| Kintamasis | Būtinas | Aprašymas |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Taip | Boto tokenas iš [@BotFather](https://t.me/BotFather) |
| `OPENROUTER_API_KEY` | Taip | OpenRouter API raktas |
| `TAVILY_API_KEY` | Taip | Tavily API raktas paieškai |
| `YOUTUBE_API_KEY` | Ne | YouTube Data API v3 raktas paieškai YouTube. Be jo boto YouTube įrankis neveikia. Nemokama kvota leidžia apie 100 paieškų per dieną |
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
