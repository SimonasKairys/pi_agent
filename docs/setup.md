# Diegimas į Raspberry Pi

Šis gidas aprašo diegimą nuo nulio į Ubuntu Server 24.04 LTS (Raspberry Pi 5). Botas veikia
kaip atskiras vartotojas `piagent` be `sudo` teisių, o jo prieigą riboja systemd.

Rekomenduojame būtent Ubuntu Server 24.04 LTS (64 bitų): visos šio gido komandos išbandytos su juo.
Sistemą į SD kortelę įrašykite su [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
**Choose OS** > **Other general-purpose OS** > **Ubuntu** > **Ubuntu Server 24.04 LTS (64-bit)**.
Nustatymuose įjunkite SSH ir nurodykite vartotojo vardą, slaptažodį ir Wi-Fi.

Raspberry Pi 5 reikia 5,1 V ir 5 A maitinimo šaltinio, pavyzdžiui, oficialaus
[27 W USB-C šaltinio](https://www.raspberrypi.com/products/27w-power-supply/). Jei prisijungus
matote `This power supply is not capable of supplying 5A`, šaltinis per silpnas, o įtampos kritimai
gali netikėtai perkrauti Pi. Įtampą patikrinsite komanda `vcgencmd get_throttled`: turi būti
`throttled=0x0`. Jei komanda nerasta, įdiekite ją su `sudo apt install libraspberrypi-bin`.

Komandas vykdykite po vieną.

## Turinys

1. [Serverio paruošimas](#serverio-paruošimas)
2. [Vartotojas `piagent`](#vartotojas-piagent)
3. [Projekto klonavimas](#projekto-klonavimas)
4. [Python aplinka](#python-aplinka)
5. [Slapti raktai](#slapti-raktai)
6. [Google Calendar per Composio](#google-calendar-per-composio)
7. [Vartotojai ir svečiai](#vartotojai-ir-svečiai)
8. [systemd paslauga](#systemd-paslauga)
9. [Paleidimas ir patikra](#paleidimas-ir-patikra)
10. [Naktinė konsolidacija ir savaitinė priežiūra](#naktinė-konsolidacija-ir-savaitinė-priežiūra)
11. [Atnaujinimas](#atnaujinimas)
12. [Diegimas iš naujo](#diegimas-iš-naujo)
13. [Teisių patikra](#teisių-patikra)
14. [Duomenų šifravimas (LUKS)](#duomenų-šifravimas-luks)
15. [Duomenų bazės patikra](#duomenų-bazės-patikra)
16. [Atsarginės kopijos](#atsarginės-kopijos)

## Serverio paruošimas

Prisijunkite prie serverio:

```bash
ssh your_username@your_hostname.local
```

Jei reikia, atnaujinkite sistemą ir perkraukite. Po perkrovimo prisijunkite iš naujo.

```bash
sudo apt update && sudo apt upgrade -y
sudo reboot
```

Įdiekite Git ir Python įrankius:

```bash
sudo apt install git python3-pip python3-venv -y
```

## Vartotojas `piagent`

Sukurkite vartotoją be `sudo` teisių ir be slaptažodžio:

```bash
sudo adduser --disabled-password --gecos "" piagent
sudo chmod 700 /home/piagent
```

Patikrinkite, kad išvestyje nėra žodžio `sudo`:

```bash
id piagent
```

## Projekto klonavimas

Aplankas `/home/piagent/telegram-agent` dar neturi egzistuoti. Jei botą jau diegėte anksčiau,
vietoj šio skyriaus atlikite [diegimą iš naujo](#diegimas-iš-naujo).

### Vieša saugykla

```bash
sudo -u piagent git clone https://github.com/SimonasKairys/pi_agent.git /home/piagent/telegram-agent
```

### Privati saugykla (deploy key)

Privačiai saugyklai reikia prieigos rakto, kuris leidžia tik skaityti:

```bash
sudo -u piagent mkdir -m 700 /home/piagent/.ssh
sudo -u piagent ssh-keygen -t ed25519 -N "" -C "raspi-deploy-key" -f /home/piagent/.ssh/id_ed25519
sudo cat /home/piagent/.ssh/id_ed25519.pub
```

1. Nukopijuokite visą eilutę (`ssh-ed25519 ... raspi-deploy-key`).
2. Saugyklos puslapyje atidarykite **Settings** > **Deploy keys** ir spustelėkite **Add deploy key**.
3. **Title** laukelyje įrašykite `raspi`, **Allow write access** nežymėkite ir spustelėkite **Add key**.

Patikrinkite ryšį. Turi būti parašyta `You've successfully authenticated`.

```bash
sudo -u piagent ssh -o StrictHostKeyChecking=accept-new -T git@github.com
```

Privataus rakto (failo be `.pub`) niekur nesiųskite. Kurdami naują raktą, pavyzdžiui, po SD
kortelės gedimo, senąjį ištrinkite tame pačiame GitHub puslapyje.

Nuklonuokite projektą:

```bash
sudo -u piagent git clone git@github.com:SimonasKairys/pi_agent.git /home/piagent/telegram-agent
```

### Be `docs/` aplanko

Kad `docs/` neatsirastų Raspberry Pi, nustatykite dalinį išskleidimą. Kiti failai ir nauji
aplankai išskleidžiami kaip įprasta.

```bash
sudo -u piagent git -C /home/piagent/telegram-agent sparse-checkout set --no-cone '/*' '!/docs/'
sudo -u piagent ls /home/piagent/telegram-agent
```

Nustatymas yra vietinis: klonuodami iš naujo, komandą pakartokite. Failai `.env` ir `dev/` į
GitHub nekeliami.

## Python aplinka

Sukurkite virtualią aplinką:

```bash
sudo -u piagent python3 -m venv /home/piagent/telegram-agent/venv
```

Įdiekite bibliotekas iš `requirements.txt`:

```bash
sudo -u piagent /home/piagent/telegram-agent/venv/bin/pip install -r /home/piagent/telegram-agent/requirements.txt
```

## Slapti raktai

Raktai laikomi ne projekto aplanke, o `root` priklausančiame faile `/etc/piagent/env`.

| Kintamasis | Kur gauti |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather), komanda `/newbot` |
| `OPENROUTER_API_KEY` | [OpenRouter raktų puslapis](https://openrouter.ai/keys). Modelis: `deepseek/deepseek-v4.1-flash` |
| `TAVILY_API_KEY` | [Tavily](https://tavily.com), paieškai internete |
| `COMPOSIO_API_KEY` | [Composio](https://app.composio.dev), su **Tool execution: Write** leidimu |
| `COMPOSIO_CONNECTED_ACCOUNT_ID` | Prijungtos Google Calendar paskyros ID (`ca_...`) |
| `YOUTUBE_API_KEY` | Nebūtina. [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com): įjunkite **YouTube Data API v3** ir sukurkite API raktą. Be jo boto YouTube paieška neveikia |

Sukurkite failą:

```bash
sudo mkdir -p /etc/piagent
sudo nano /etc/piagent/env
```

Įklijuokite savo reikšmes be kabučių ir be tarpų aplink `=`:

```ini
OPENROUTER_API_KEY=jusu_openrouter_raktas
TELEGRAM_BOT_TOKEN=jusu_telegram_tokenas
TAVILY_API_KEY=jusu_tavily_raktas
COMPOSIO_API_KEY=jusu_composio_raktas
COMPOSIO_CONNECTED_ACCOUNT_ID=prijungtos_paskyros_id
# Nebūtina, YouTube paieškai:
YOUTUBE_API_KEY=jusu_youtube_raktas
```

Apribokite teises ir patikrinkite. Komanda rodo tik pavadinimus:

```bash
sudo chown root:root /etc/piagent/env
sudo chmod 600 /etc/piagent/env
sudo cut -d= -f1 /etc/piagent/env
```

## Google Calendar per Composio

1. [Composio](https://app.composio.dev) prijunkite Google Calendar paskyrą. Google sutikimo lange
   palikite pažymėtus visus leidimus ir patvirtinkite pilną kalendoriaus leidimą
   `https://www.googleapis.com/auth/calendar`. Vien `calendar.events` nepakanka.
   Jei naudojate savo Google OAuth programą, tą patį leidimą pridėkite prie sutikimo ekrano,
   o testavimo režime savo paskyrą įtraukite į **Test users**.
2. Prijungtos paskyros ID (`ca_...`) nukopijuokite į `COMPOSIO_CONNECTED_ACCOUNT_ID`.
   Perjungus paskyrą, ID paprastai pasikeičia.
3. `COMPOSIO_API_KEY` turi turėti **Tool execution: Write** leidimą.
4. Jei botas jau veikia, pakeitę `/etc/piagent/env` paleiskite `sudo systemctl restart piagent`.

Jei kalendorius grąžina `insufficient authentication scopes` (`ACCESS_TOKEN_SCOPE_INSUFFICIENT`,
`calendar.v3.Calendars.Get`), Google leidimai per siauri. Tokiu atveju skaitymas gali veikti, o
kūrimas ne. Composio iš naujo prijunkite paskyrą su pilnu leidimu ir atnaujinkite ID.

Įrankiai siunčia vietinį laiką be UTC poslinkio ir atskirą laiko zoną (`Europe/Vilnius`). Jei
įvykis atsiranda 3 valandomis anksčiau, patikrinkite, ar įdiegtas naujausias kodas.

## Vartotojai ir svečiai

Botu gali naudotis tik vartotojai, išvardyti `/etc/piagent/users.toml`. Jei atlikote
[duomenų šifravimą](#duomenų-šifravimas-luks), failas yra `/home/piagent/data/users.toml`, todėl
toliau esančiose komandose naudokite šį kelią. Kiekvienas vartotojas turi
vardą, el. pašto adresą ir laiko juostą. Savo Telegram ID sužinosite parašę botui
[@userinfobot](https://t.me/userinfobot).

```bash
sudo nano /etc/piagent/users.toml
```

```toml
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "vardas@example.com"
timezone = "Europe/Vilnius"
role = "admin"

[[user]]
telegram_id = 222222222
name = "Ruta"
email = "ruta@example.com"
timezone = "Europe/Vilnius"
role = "member"
language = "en"

# Svečiai (nebūtini): juos galima kviesti į įvykius, bet botu jie naudotis negali.
[[guest]]
name = "Jonas"
email = "jonas@example.org"
```

- `language` nebūtinas: `lt` (numatytoji) arba `en`. Juo botas atsako, rodo `/pagalba`, patvirtinimo
  korteles, priminimus ir komandų meniu. Anglakalbiams komandos yra `/help` ir `/costs`, bet
  `/pagalba` ir `/islaidos` veikia visiems. Svečiams kalbos nustatyti nereikia, nes botu jie
  nesinaudoja: kvietimus jiems siunčia Google jų paskyros kalba.
- Vardai turi būti unikalūs tarp vartotojų ir svečių, nes modelis dalyvius nurodo vardais.
- Agentas kviečia tik žmones iš šio sąrašo. El. pašto adreso, kurio sąraše nėra, pridėti jis negali.
- Pašalinus svečią iš failo, įvykių, kuriuose jis dalyvauja, keisti nebegalima, kol jo
  nepašalinsite iš įvykio arba nepridėsite atgal.

Apribokite teises, kad procesas galėtų failą skaityti, bet ne perrašyti:

```bash
sudo chown root:piagent /etc/piagent/users.toml
sudo chmod 640 /etc/piagent/users.toml
```

Jei botas jau veikia, pakeitę failą paleiskite `sudo systemctl restart piagent`.

Jei faile yra klaida (pasikartojantis vardas, neteisinga laiko juosta, el. paštas be `@`), botas
nestartuoja ir priežastį įrašo į žurnalą:

```bash
sudo journalctl -u piagent -n 20 --no-pager
```

## systemd paslauga

Paslaugų failai yra projekto aplanke `deploy/`, todėl jų kopijuoti iš šio gido nereikia. Įdiekite
juos visus iš karto: boto paslaugą, naktinę konsolidaciją, savaitinę priežiūrą ir atsargines
kopijas.

```bash
sudo sh -c 'install -m 644 -t /etc/systemd/system /home/piagent/telegram-agent/deploy/piagent*.service /home/piagent/telegram-agent/deploy/piagent*.timer'
sudo systemd-analyze verify /etc/systemd/system/piagent*.service /etc/systemd/system/piagent*.timer
```

`sudo sh -c` reikalingas, nes jūsų vartotojas negali skaityti `/home/piagent`. Be jo apvalkalas
neišplėstų `*` ir `install` praneštų `cannot stat`.

Jei antroji komanda nieko neišveda, failai geri. Jei dar neįdiegėte
[atsarginių kopijų](#atsarginės-kopijos) skripto, pranešimas apie
`/usr/local/lib/piagent-backup/venv/bin/python` yra tikėtinas.

`install` failus nukopijuoja, o ne susieja. Susieti nevalia: projekto aplankas priklauso
`piagent`, todėl botas galėtų perrašyti savo paslaugą ir panaikinti jos apribojimus.

## Paleidimas ir patikra

Paleiskite botą dabar ir po kiekvieno perkrovimo. Paskutinė komanda turi išvesti `active`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piagent
sudo systemctl is-active piagent
```

Parašykite botui Telegram `/start`, tada bet kokį klausimą.

### Dažnos klaidos

Žurnalą peržiūrėsite su `sudo journalctl -u piagent -n 50 --no-pager`.

| Klaida | Priežastis |
|---|---|
| `KeyError 'OPENROUTER_API_KEY'` | Neteisingas `/etc/piagent/env` |
| `No module named ...` | Pakartokite `pip install` |
| `Permission denied (publickey)` klonuojant | Deploy key nepridėtas arba nukopijuotas ne visas |
| `Conflict: terminated by other getUpdates` | Tas pats botas veikia kitur |

### Kasdienės komandos

```bash
sudo systemctl status piagent --no-pager
sudo journalctl -u piagent -f        # išėjimas: Ctrl+C
sudo systemctl restart piagent       # po kodo ar env pakeitimų
sudo systemctl stop piagent
sudo piagent-unlock                  # po perkrovimo, jei naudojate šifruotą diską
```

Telegram komandos: `/start` pasisveikina, `/pagalba` parodo, ką botas moka, su pavyzdžiais, o
`/islaidos` parodo šiandienos išlaidas ir dienos ribas. `/pagalba` ir `/islaidos` matomos ir
Telegram komandų meniu (mygtukas **/**).

Kiekvienas vartotojas turi bent kartą parašyti botui `/start`. Kitaip Telegram neleidžia botui
pirmam atsiųsti žinutės, ir kitų sukurti priminimai tam vartotojui nepasieks. Kūrėjas tada gauna
pranešimą, kad priminimo išsiųsti nepavyko.

## Naktinė konsolidacija ir savaitinė priežiūra

Abi užduotys veikia kaip atskiros paslaugos, ne boto dalis. Jų failus įdiegėte kartu su
[systemd paslauga](#systemd-paslauga), todėl liko juos įjungti.

Konsolidacija vyksta kas naktį 03:00, 30 minučių prieš atsarginę kopiją 03:30, kad įrašai patektų į
tos nakties kopiją. Ji sutraukia senas žinutes į santraukas ir sutvarko faktus apie vartotojus.

Kas sekmadienį 03:15, tarp konsolidacijos (03:00) ir atsarginės kopijos (03:30), paleidžiamas
modulis `agent.maintenance`. Jis:

1. Ištrina senesnes nei 120 dienų žinutes, bet tik tas, kurios jau sutrauktos į santrauką.
   Nesutrauktos žinutės lieka bet kokio amžiaus, o faktai apie vartotoją netrinami.
2. Ištrina užbaigtus patvirtinimus (patvirtintus, atmestus, pasibaigusius), senesnius nei 30 dienų.
3. Ištrina išsiųstus ir neišsiųstus (nepasiekusius gavėjo) priminimus, senesnius nei 30 dienų.
   Laukiantys priminimai ir užrašai netrinami.
4. Iš žurnalo `journal.jsonl` pašalina senesnes nei 90 dienų eilutes.
5. Suspaudžia duomenų bazę: `PRAGMA wal_checkpoint(TRUNCATE)`, `PRAGMA optimize` ir `VACUUM`.

Pašalintų žinučių atkurti neįmanoma: botas jas atsimena tik iš santraukos ir faktų. Iki 14 dienų
senumo duomenys dar yra atsarginėse kopijose.

Laikas nurodytas su zona (`Europe/Vilnius`), kitaip `OnCalendar` naudoja sistemos zoną.

Jei naudojate [šifruotą diską](#duomenų-šifravimas-luks), neleiskite priežiūrai veikti, kol diskas
neatrakintas:

```bash
sudo mkdir -p /etc/systemd/system/piagent-maintenance.service.d
printf '[Unit]\nConditionPathIsMountPoint=/home/piagent/data\n' \
  | sudo tee /etc/systemd/system/piagent-maintenance.service.d/encrypted-data.conf > /dev/null
```

Įjunkite laikmačius. Paskutinė komanda turi rodyti artimiausią 03:00 ir artimiausią sekmadienį
03:15 (EEST arba EET).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piagent-consolidate.timer piagent-maintenance.timer
systemctl list-timers 'piagent-*'
```

Paleiskite konsolidacijos bandymą dabar. Pabaigoje turi būti `Naktine konsolidacija baigta`.
Pranešimas `zinuciu nera` reiškia, kad per parą pokalbių nebuvo.

```bash
sudo systemctl start piagent-consolidate.service
sudo journalctl -u piagent-consolidate.service -n 15 --no-pager
```

Paleiskite priežiūros bandymą. Pabaigoje turi būti `Duomenų priežiūra baigta` su ištrintų įrašų
skaičiais.

```bash
sudo systemctl start piagent-maintenance.service
sudo journalctl -u piagent-maintenance.service -n 10 --no-pager
```

| Klaida | Priežastis |
|---|---|
| `Refusing to start, unit ... not loaded` | Paslaugos failas neįdiegtas. Pakartokite [systemd paslaugos](#systemd-paslauga) `install` komandą |
| `Missing credentials` | Paslaugos faile trūksta `EnvironmentFile=/etc/piagent/env` |

`Persistent=true` po įjungimo gali paleisti papildomą praleistą paleidimą. Tai nekenksminga.

## Atnaujinimas

Išsiuntę pakeitimus į GitHub (`git push`), Raspberry Pi parsisiųskite naują kodą. Jei naudojate
šifruotą diską ir Raspberry Pi buvo perkrautas, pirmiausia paleiskite `sudo piagent-unlock`.

```bash
sudo -u piagent git -C /home/piagent/telegram-agent pull
```

Jei `pull` nepavyksta dėl vietinių pakeitimų, patikrinkite
`sudo -u piagent git -C /home/piagent/telegram-agent status`.

Jei `pull` išvestyje matote `deploy/`, įdiekite naujus paslaugų failus:

```bash
sudo sh -c 'install -m 644 -t /etc/systemd/system /home/piagent/telegram-agent/deploy/piagent*.service /home/piagent/telegram-agent/deploy/piagent*.timer'
sudo systemctl daemon-reload
```

Jei matote `backup/backup.py`, įdiekite naują kopijavimo skriptą:

```bash
sudo install -o root -g root -m 755 /home/piagent/telegram-agent/backup/backup.py /usr/local/lib/piagent-backup/backup.py
```

Jei matote `requirements.txt`, atnaujinkite bibliotekas:

```bash
sudo -u piagent /home/piagent/telegram-agent/venv/bin/python -m pip install -r /home/piagent/telegram-agent/requirements.txt
```

Paleiskite botą iš naujo. Paskutinė komanda turi išvesti `active`.

```bash
sudo systemctl restart piagent
sudo systemctl is-active piagent
```

Kūrimo failus kompiuteryje laikykite `dev/` aplanke, nes jis į GitHub nekeliamas.

## Diegimas iš naujo

Šis skyrius skirtas, kai `/home/piagent/telegram-agent` jau yra, pavyzdžiui, kai norite švaraus
kodo aplanko. Duomenys, raktai ir vartotojų sąrašas lieka savo vietose, nes jie laikomi ne kodo
aplanke.

Sustabdykite botą ir pervadinkite seną aplanką:

```bash
sudo systemctl stop piagent
sudo -u piagent mv /home/piagent/telegram-agent /home/piagent/telegram-agent.old
```

Nuklonuokite projektą, kaip aprašyta skyriuje [Projekto klonavimas](#projekto-klonavimas). Tada
perkelkite seną Python aplinką, kad nereikėtų visko siųstis iš naujo, ir atnaujinkite bibliotekas:

```bash
sudo -u piagent mv /home/piagent/telegram-agent.old/venv /home/piagent/telegram-agent/venv
sudo -u piagent /home/piagent/telegram-agent/venv/bin/python -m pip install -r /home/piagent/telegram-agent/requirements.txt
```

Čia naudojamas `python -m pip`, o ne `venv/bin/pip`: perkeltos aplinkos `pip` vis dar rodo į seną
aplanką ir neveikia.

Jei senos aplinkos nėra, ją sukurkite pagal skyrių [Python aplinka](#python-aplinka).

Įdiekite paslaugų failus ir paleiskite botą:

```bash
sudo sh -c 'install -m 644 -t /etc/systemd/system /home/piagent/telegram-agent/deploy/piagent*.service /home/piagent/telegram-agent/deploy/piagent*.timer'
sudo systemctl daemon-reload
sudo systemctl start piagent
sudo systemctl is-active piagent
```

Kai botas veikia, seną aplanką ištrinkite:

```bash
sudo rm -r /home/piagent/telegram-agent.old
```

## Teisių patikra

Botas turi galėti rašyti tik į `/home/piagent`, o `sudo` jam neturi veikti:

```bash
sudo tee /tmp/piagent-test.sh > /dev/null <<-'EOF'
echo "kas as: $(whoami)"
touch /home/piagent/testas && echo "namai: PAVYKO" || echo "namai: NEPAVYKO"
touch /etc/testas 2>/dev/null && echo "etc: PAVYKO (blogai)" || echo "etc: NEPAVYKO (gerai)"
touch /usr/testas 2>/dev/null && echo "usr: PAVYKO (blogai)" || echo "usr: NEPAVYKO (gerai)"
echo "namai: $(ls /home | tr '\n' ' ')"
ls /media >/dev/null 2>&1 && echo "media: matoma" || echo "media: nepasiekiama (gerai)"
sudo -n true 2>/dev/null && echo "sudo: VEIKIA (blogai)" || echo "sudo: neveikia (gerai)"
rm -f /home/piagent/testas
EOF
sudo chmod 644 /tmp/piagent-test.sh
sudo systemd-run --wait --pipe --uid=piagent -p ProtectSystem=strict -p ReadWritePaths=/home/piagent -p ProtectHome=tmpfs -p BindPaths=/home/piagent -p InaccessiblePaths="/root /media /mnt /srv" bash /tmp/piagent-test.sh
sudo rm /tmp/piagent-test.sh
```

## Duomenų šifravimas (LUKS)

Šis skyrius nebūtinas, bet rekomenduojamas. Jis užšifruoja visą asmeninę informaciją: duomenų bazę
(žinutes, faktus, įvykius), žurnalą ir `users.toml` (vardus, el. pašto adresus, Telegram ID).
Duomenys laikomi šifruotame LUKS2 konteineryje (AES-256), o slaptažodį įvedate po kiekvieno
perkrovimo. Kol slaptažodžio neįvedėte, botas neveikia, o pavogtoje SD kortelėje duomenų perskaityti
neįmanoma.

Konteineris yra failas, todėl SD kortelės skaidinių keisti nereikia.

| Kas | Kelias po šifravimo |
|---|---|
| Duomenų bazė | `/home/piagent/data/db/agent.db` |
| Žurnalas | `/home/piagent/data/logs/` |
| Vartotojai | `/home/piagent/data/users.toml` |
| Šifruotas konteineris | `/var/lib/piagent/data.luks` |

Slaptažodį išsaugokite slaptažodžių tvarkyklėje: be jo duomenų atkurti neįmanoma.

### 1. Sustabdykite botą

```bash
sudo apt install -y cryptsetup
sudo systemctl stop piagent piagent-consolidate.timer piagent-maintenance.timer
```

Jei naudojate atsarginių kopijų laikmatį, sustabdykite ir jį.

### 2. Sukurkite konteinerį

Komanda `luksFormat` paprašys įrašyti `YES` didžiosiomis raidėmis ir du kartus įvesti slaptažodį.
Dydis `2G` yra pakankamas daugeliui metų. Jį galite padidinti.

```bash
sudo install -d -m 700 /var/lib/piagent
sudo fallocate -l 2G /var/lib/piagent/data.luks
sudo cryptsetup luksFormat --type luks2 /var/lib/piagent/data.luks
sudo cryptsetup open /var/lib/piagent/data.luks piagent-data
sudo mkfs.ext4 -q /dev/mapper/piagent-data
```

### 3. Prijunkite ir paruoškite katalogus

Senus duomenis pirmiausia pervadinkite, kad jie liktų, kol patikrinsite naują vietą:

```bash
sudo mv /home/piagent/data /home/piagent/data.plain
sudo install -d -o root -g piagent -m 750 /home/piagent/data
sudo mount /dev/mapper/piagent-data /home/piagent/data
sudo chown root:piagent /home/piagent/data
sudo chmod 750 /home/piagent/data
sudo install -d -o piagent -g piagent -m 700 /home/piagent/data/db /home/piagent/data/logs
```

Viršutinis katalogas priklauso `root`, todėl botas negali pakeisti `users.toml`. Į `db/` ir `logs/`
jis rašyti gali.

### 4. Perkelkite duomenis

```bash
sudo -u piagent cp -a /home/piagent/data.plain/. /home/piagent/data/db/
sudo test -d /home/piagent/logs && sudo -u piagent cp -a /home/piagent/logs/. /home/piagent/data/logs/
sudo cp /etc/piagent/users.toml /home/piagent/data/users.toml
sudo chown root:piagent /home/piagent/data/users.toml
sudo chmod 640 /home/piagent/data/users.toml
sudo ls -la /home/piagent/data /home/piagent/data/db
```

Sąraše turi būti `users.toml`, `db/agent.db` ir `logs/`.

### 5. Nukreipkite botą į naujus kelius

```bash
sudo tee -a /etc/piagent/env > /dev/null <<'EOF'
PIAGENT_DB_PATH=/home/piagent/data/db/agent.db
PIAGENT_LOG_DIR=/home/piagent/data/logs
PIAGENT_USERS_FILE=/home/piagent/data/users.toml
EOF
sudo cut -d= -f1 /etc/piagent/env
```

Jei naudojate atsarginių kopijų skriptą, į `/etc/piagent/backup.env` įrašykite tą patį
`PIAGENT_DB_PATH`.

### 6. Neleiskite botui startuoti be šifruoto disko

Be šio žingsnio botas po perkrovimo sukurtų naują tuščią duomenų bazę nešifruotame diske.

```bash
for unit in piagent piagent-consolidate piagent-maintenance; do
  sudo mkdir -p /etc/systemd/system/$unit.service.d
  printf '[Unit]\nConditionPathIsMountPoint=/home/piagent/data\n' \
    | sudo tee /etc/systemd/system/$unit.service.d/encrypted-data.conf > /dev/null
done
sudo systemctl daemon-reload
```

### 7. Atrakinimo komanda

```bash
sudo tee /usr/local/sbin/piagent-unlock > /dev/null <<'EOF'
#!/bin/sh
# Atrakina šifruotus boto duomenis ir paleidžia botą. Vykdoma po kiekvieno perkrovimo.
set -e
if ! mountpoint -q /home/piagent/data; then
  cryptsetup open /var/lib/piagent/data.luks piagent-data
  mount /dev/mapper/piagent-data /home/piagent/data
fi
systemctl start piagent piagent-consolidate.timer
systemctl is-active piagent
EOF
sudo chmod 755 /usr/local/sbin/piagent-unlock
```

### 8. Paleiskite ir patikrinkite

```bash
sudo systemctl start piagent piagent-consolidate.timer
sudo systemctl is-active piagent
sudo -u piagent sqlite3 -readonly /home/piagent/data/db/agent.db "PRAGMA integrity_check;"
```

Turi būti `active` ir `ok`. Parašykite botui žinutę ir paklauskite, ką jis apie jus atsimena: atsakymas
turi remtis senais faktais.

Perkraukite Raspberry Pi ir patikrinkite, ar be slaptažodžio botas nestartuoja:

```bash
sudo reboot
```

Prisijungę iš naujo paleiskite:

```bash
systemctl is-active piagent
sudo piagent-unlock
```

Pirmoji komanda turi išvesti `inactive`, o po slaptažodžio įvedimo `piagent-unlock` turi išvesti
`active`.

### 9. Ištrinkite nešifruotas kopijas

Tai darykite tik įsitikinę, kad botas veikia su senais duomenimis.

```bash
sudo find /home/piagent/data.plain -type f -exec shred -u {} +
sudo rm -r /home/piagent/data.plain
sudo test -d /home/piagent/logs && sudo find /home/piagent/logs -type f -exec shred -u {} + && sudo rm -r /home/piagent/logs
sudo shred -u /etc/piagent/users.toml
```

SD kortelėse `shred` negarantuoja, kad seni duomenys išnyks fiziškai, nes kortelės valdiklis
įrašus paskirsto savaip. Jei kortelėje ilgai buvo jautrių duomenų, saugiausia sistemą perkelti į
naują kortelę ir senąją sunaikinti.

### 10. Įspėjimas per Telegram, kai diskas užrakintas

Šis žingsnis nebūtinas, bet rekomenduojamas. Po netikėto perkrovimo, pavyzdžiui, dingus elektrai,
botas neveikia, kol neatrakinate disko. Paslauga `piagent-unlock-alert` po paleidimo patikrina, ar
diskas prijungtas. Jei ne, ji per Telegram atsiunčia žinutę su priminimu paleisti
`sudo piagent-unlock`. Nepavykus prisijungti prie tinklo, ji bando iš naujo apie 10 minučių. Jei
diskas jau atrakintas, žinutė nesiunčiama.

Po perkrovimo `users.toml` užrakintas, todėl gavėjų ID ir kalba laikomi `/etc/piagent/env`.
Paprastai užtenka administratoriaus ID. Jį rasite `users.toml` lauke `telegram_id`:

```bash
sudo grep telegram_id /home/piagent/data/users.toml
```

Įrašykite ID ir kalbą (`lt` arba `en`). Kelis ID atskirkite kableliais.

```bash
sudo tee -a /etc/piagent/env > /dev/null <<'EOF'
PIAGENT_UNLOCK_ALERT_CHAT_IDS=111111111
PIAGENT_UNLOCK_ALERT_LANGUAGE=lt
EOF
```

Įdiekite ir įjunkite paslaugą:

```bash
sudo install -m 644 -t /etc/systemd/system /home/piagent/telegram-agent/deploy/piagent-unlock-alert.service
sudo systemctl daemon-reload
sudo systemctl enable piagent-unlock-alert.service
```

Be šifravimo šios paslaugos neįjunkite: tada `/home/piagent/data` nėra atskiras prijungimo taškas,
ir žinutė ateitų po kiekvieno perkrovimo.

Patikrinkite perkraudami Raspberry Pi su `sudo reboot`. Per kelias minutes turi ateiti žinutė. Jei
ji neatėjo, prisijunkite ir peržiūrėkite žurnalą:

```bash
journalctl -b -u piagent-unlock-alert
```

### 11. Nuotolinis atrakinimas per Tailscale

Kad diską galėtumėte atrakinti ne namuose, prie Pi junkitės per [Tailscale](https://tailscale.com/).
Tai privatus tinklas tarp jūsų įrenginių: maršrutizatoriuje nereikia atidaryti prievadų, o Pi
internete nematomas. Neatidarykite SSH prievado 22 maršrutizatoriuje: jį nuolat bando nulaužti
automatiniai skeneriai.

Įdiekite Tailscale ir prisijunkite. `tailscale up` parodo adresą, kurį reikia atidaryti naršyklėje:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
```

Paskutinė komanda parodo Pi adresą, prasidedantį `100.`. Jis nesikeičia. Tailscale pasileidžia su
sistema ir nuo šifruoto disko nepriklauso, todėl veikia ir tada, kai botas laukia atrakinimo.

Įrenginio raktas pagal numatymą baigia galioti po 180 dienų, ir tada prieiga dingsta.
[Tailscale administravimo konsolėje](https://login.tailscale.com/admin/machines) prie Pi
spustelėkite **...** > **Disable key expiry**.

Telefone įdiekite Tailscale programėlę ir prisijunkite ta pačia paskyra. SSH programėlė gali būti
ConnectBot (Android, nemokama) arba Termius (iOS ir Android, nemokamo plano pakanka). Prisijungime
nurodykite `admin@100.x.x.x`, kur `admin` yra jūsų Pi vartotojas, o adresas iš `tailscale ip -4`.

Patikrinkite prieš prireikiant: telefone išjunkite Wi-Fi, prisijunkite per mobilųjį internetą ir
paleiskite `sudo piagent-unlock`. Gavę [įspėjimą per Telegram](#10-įspėjimas-per-telegram-kai-diskas-užrakintas),
darykite tą patį.

### Kas lieka nešifruota

- `/etc/piagent/env`: API raktai, prieinami tik `root`.
- `journalctl` žurnalas: jame gali pasitaikyti vartotojų vardų ir klaidų tekstų, bet ne žinučių
  turinys.
- Duomenys trečiųjų šalių serveriuose (OpenRouter, Composio, Google, Tavily).

## Duomenų bazės patikra

Duomenų bazė yra `/home/piagent/data/db/agent.db`, jei atlikote šifravimą, arba
`/home/piagent/data/agent.db`, jei ne. Lentelės: `messages`, `summaries`, `facts`, `events`,
`event_attendees`, `event_guests`, `pending_approvals` ir `usage`.

Naudokite `-readonly` ir `sudo -u piagent`. Nekeiskite duomenų, kol botas veikia. Pirmiausia
nustatykite kelią. Jei šifravimo neatlikote, naudokite `/home/piagent/data/agent.db`.

```bash
DB=/home/piagent/data/db/agent.db
sudo apt install -y sqlite3
sudo -u piagent sqlite3 -readonly "$DB" ".tables"
```

Įrašų skaičius:

```bash
sudo -u piagent sqlite3 -readonly "$DB" "SELECT 'messages', COUNT(*) FROM messages UNION ALL SELECT 'summaries', COUNT(*) FROM summaries UNION ALL SELECT 'facts', COUNT(*) FROM facts UNION ALL SELECT 'events', COUNT(*) FROM events UNION ALL SELECT 'usage', COUNT(*) FROM usage;"
```

Naujausi įrašai:

```bash
sudo -u piagent sqlite3 -readonly -header -column "$DB" "SELECT * FROM messages ORDER BY rowid DESC LIMIT 5;"
sudo -u piagent sqlite3 -readonly -header -column "$DB" "SELECT id, title, starts_at, deleted_at FROM events ORDER BY id DESC LIMIT 5;"
```

Vientisumas. Turi būti `ok`:

```bash
sudo -u piagent sqlite3 -readonly "$DB" "PRAGMA integrity_check;"
```

Parašykite botui žinutę ir pakartokite `messages` skaičiavimą: skaičius turi padidėti. Ištrintas
įvykis lieka `events` lentelėje su užpildytu `deleted_at` (minkštas trynimas).

## Atsarginės kopijos

Skriptas `backup/backup.py` kas naktį 03:30 padaro duomenų bazės kopiją, užšifruoja ją `gpg` ir
įkelia į Google Drive. Senesnes nei 14 dienų kopijas jis ištrina. Skriptas veikia kaip `root` iš
atskiro katalogo, kad botas negalėtų perskaityti kopijavimo raktų ar pakeisti skripto.

### Skripto diegimas

```bash
sudo apt install -y gnupg
sudo install -d -o root -g root -m 755 /usr/local/lib/piagent-backup
sudo install -o root -g root -m 755 /home/piagent/telegram-agent/backup/backup.py /usr/local/lib/piagent-backup/backup.py
sudo python3 -m venv /usr/local/lib/piagent-backup/venv
sudo /usr/local/lib/piagent-backup/venv/bin/python -m pip install -r /home/piagent/telegram-agent/backup/requirements.txt
```

Google raktams reikia Google Cloud projekto su įjungtu Google Drive API:

1. **Google Auth Platform** > **Branding** užpildykite programos pavadinimą, el. paštą, pagrindinio
   puslapio ir privatumo politikos adresus. Logotipo neįkelkite: su juo Google reikalauja patikros.
2. **Data Access** pridėkite tik leidimą `https://www.googleapis.com/auth/drive.file`.
3. **Audience** spustelėkite **Publish app**. Būsena turi būti **In production**. Testavimo
   režimu (**Testing**) `GOOGLE_REFRESH_TOKEN` galioja tik 7 dienas, ir kopijavimas nustoja veikti.
4. **Clients** sukurkite **Desktop app** tipo klientą. Kliento ID ir paslaptį (**Client secret**)
   išsaugokite slaptažodžių tvarkyklėje: vėliau Google paslapties nebeparodo, galima tik pridėti
   naują (**Add secret**).

Tokeną gaukite kompiuteryje su naršykle, ne Raspberry Pi. Skriptas paklausia kliento ID ir
paslapties (paslaptis ekrane nerodoma), atidaro Google prisijungimą ir išveda `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` ir `GOOGLE_REFRESH_TOKEN`:

```bash
python3 -m venv /tmp/piagent-oauth
/tmp/piagent-oauth/bin/python -m pip install google-auth-oauthlib
/tmp/piagent-oauth/bin/python backup/authorize.py
```

Jei atsisiuntėte kliento JSON failą, jo kelią galite nurodyti kaip argumentą.

Tokeną galima gauti ir tiesiai Raspberry Pi. Tada skriptas parodo adresą, kurį atidarote bet kurio
įrenginio naršyklėje. Po prisijungimo naršyklė praneša, kad `localhost` nepasiekiamas. Nukopijuokite
visą adresą iš adreso juostos ir įklijuokite į Pi terminalą. Skriptas pats įrašo raktus į
`backup.env`:

```bash
sudo /usr/local/lib/piagent-backup/venv/bin/python /home/piagent/telegram-agent/backup/authorize.py --headless --env-file /etc/piagent/backup.env
```

Kai skriptas klausia `GOOGLE_CLIENT_ID`, paspauskite **Enter**, kad liktų esamas ID.

Jei naršyklė rodo „Google hasn't verified this app“, spustelėkite **Advanced** ir
**Go to ... (unsafe)**: tai jūsų pačių programa.

Raktus įrašykite į `/etc/piagent/backup.env`. `PIAGENT_DB_PATH` turi sutapti su boto
`/etc/piagent/env` reikšme, kitaip skriptas duomenų bazės neras.

```bash
sudo tee /etc/piagent/backup.env > /dev/null <<'EOF'
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
BACKUP_FOLDER_ID=
BACKUP_PASSPHRASE=
PIAGENT_DB_PATH=/home/piagent/data/db/agent.db
EOF
sudo chown root:root /etc/piagent/backup.env
sudo chmod 600 /etc/piagent/backup.env
sudo nano /etc/piagent/backup.env
```

Jei šifravimo neatlikote, `PIAGENT_DB_PATH` yra `/home/piagent/data/agent.db`.
`BACKUP_PASSPHRASE` išsaugokite slaptažodžių tvarkyklėje: be jo kopijos neatkuriamos.

### Kasnaktinis kopijavimas

Paslaugos ir laikmačio failus įdiegėte kartu su [systemd paslauga](#systemd-paslauga). Pirmiausia
paleiskite kopijavimą ranka ir patikrinkite žurnalą. Paskutinė eilutė turi būti
`Atsarginė kopija sėkmingai atlikta.`, o kopija turi atsirasti Google Drive kataloge.

```bash
sudo systemctl start piagent-backup.service
sudo journalctl -u piagent-backup -n 20 --no-pager
```

Tada įjunkite laikmatį. Paskutinė komanda turi rodyti artimiausią 03:30.

```bash
sudo systemctl enable --now piagent-backup.timer
systemctl list-timers --no-pager 'piagent-*'
```

Jei Raspberry Pi 03:30 buvo išjungtas, laikmatis kopiją paleidžia iškart po įjungimo
(`Persistent=true`). Šifruotame diske ji nepavyks, nes diskas tuo metu dar užrakintas.

### Kopijavimo teisių patikra

`piagent` neturi galėti skaityti kopijavimo raktų ir keisti kopijavimo skripto. Pirmoji komanda
turi grąžinti `Permission denied`, o kitos dvi `exit=1`:

```bash
sudo -u piagent cat /etc/piagent/backup.env
sudo -u piagent test -w /usr/local/lib/piagent-backup/backup.py; echo "exit=$?"
sudo -u piagent test -w /usr/local/lib/piagent-backup; echo "exit=$?"
```

Jei matote `exit=0`, tam failui ar katalogui paleiskite `sudo chown root:root` ir `sudo chmod 755`.

Šifruotame diske kopijavimas veikia tik tada, kai diskas atrakintas. Jei Raspberry Pi buvo perkrautas
ir `piagent-unlock` dar nepaleistas, 03:30 kopija nepavyks.

### Rankinė kopija

Kodo ir paslaugų failų (`deploy/`) kopija yra GitHub. Joje nėra `/etc/piagent/env` (raktų) ir
`venv`: raktus saugokite slaptažodžių tvarkyklėje, o `venv` atkurkite pagal šį gidą.

Pilna duomenų kopija šifruojama `gpg`. Komanda paprašys du kartus įvesti kopijos slaptažodį:

```bash
sudo tar czf - -C /home/piagent data | gpg --symmetric --cipher-algo AES256 -o ~/piagent-backup-$(date +%F).tar.gz.gpg
```

Savo kompiuteryje:

```bash
scp your_username@your_hostname.local:~/piagent-backup-*.tar.gz.gpg .
gpg -d piagent-backup-DATA.tar.gz.gpg | tar xzf -
```

Vietoj `DATA` įrašykite kopijos datą.
