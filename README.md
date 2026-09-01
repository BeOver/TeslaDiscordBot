# Tesla Discord Status Bot

Discord-Bot, der den Namen eines Channels automatisch an den aktuellen Tesla-Status anpasst und beendete Fahrten als Fahrtenbuch-Eintrag postet.

| Anzeige     | Bedeutung                  |
|-------------|----------------------------|
| ⚫67%       | Offline oder Sleep         |
| 🛠️72%       | Service / Werkstatt        |
| 🟢82%       | Online, geparkt            |
| 🔵74%       | Unterwegs (Driving)        |
| 🟠68%       | Lädt                       |
| 🔴71%       | Wächtermodus (Sentry)      |

Der Bot nutzt **TeslaPy** (Tesla Owner API) und aktualisiert den Channel-Namen nur bei echten Änderungen – mit Cooldown, um Discords Rate-Limits (~2 Renames / 10 Min.) einzuhalten.

Zusätzlich erkennt der Bot den Beginn und das Ende einer Fahrt und schreibt nach Fahrtende automatisch eine Zusammenfassung (Fahrtenbuch) in den Status-Channel.

---

## Voraussetzungen

* Python 3.10+
* Tesla-Account mit Fahrzeug
* Discord-Bot mit **Manage Channels**- und **Send Messages**-Berechtigung

---

## 1. Discord-Bot erstellen

1. Öffne das [Discord Developer Portal](https://discord.com/developers/applications).
2. **New Application** → Name wählen → **Bot** → **Add Bot**.
3. Token kopieren → `DISCORD_TOKEN` in `.env`.
4. Unter **Privileged Gateway Intents** reichen die Standard-Intents (Guilds).
5. **OAuth2 → URL Generator**:
   * Scopes: `bot`, `applications.commands`
   * Bot Permissions: `Manage Channels`, `Send Messages`
6. Bot auf deinen Server einladen.

### Status-Channel vorbereiten

* Erstelle einen Text- oder Voice-Channel für den Status (z. B. `#tesla-status`).
* Rechtsklick → **Link kopieren** → die Channel-ID ist die lange Zahl in der URL.
* Diese ID als `CHANNEL_ID` in `.env` eintragen.

---

## 2. Projekt einrichten

```bash
cd tesla-discord-status
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

`.env` ausfüllen (siehe `.env.example`).

---

## 3. Tesla-Authentifizierung

**Wichtig:** TeslaPy nutzt einen Browser-Login. Nach „Verified Successfully“ im Browser ist der Login **noch nicht fertig** – du musst die URL zurück ins Terminal kopieren!

### Empfohlen: Separates Login-Skript

```bash
python tesla_auth.py
```

Ablauf:

1. Browser öffnet sich → bei Tesla einloggen
2. Browser zeigt **„Verified Successfully“** oder **„Page Not Found“** → das ist normal
3. **Komplette URL** aus der Browser-Adresszeile kopieren  
   (z. B. `https://auth.tesla.com/void/callback?code=...&state=...`)
4. In das **Terminal zurückwechseln** → URL einfügen → Enter
5. `tesla_cache.json` wird erstellt

Danach:

```bash
python bot.py
```

### Alternative: Login beim Bot-Start

Beim ersten `python bot.py` erscheinen dieselben Anweisungen im Terminal **bevor** Discord verbindet. Wichtig: Terminal-Fenster im Blick behalten und URL einfügen, sobald der Browser fertig ist.

### Refresh-Token manuell (optional)

Wenn du den Refresh-Token bereits hast, trage ihn in `.env` ein:

```env
TESLA_REFRESH_TOKEN=dein_refresh_token
```

Alternativ kannst du `tesla_cache.json` manuell befüllen, nachdem du dich einmal eingeloggt hast.

### Mehrere Fahrzeuge

```env
TESLA_VIN=5YJ3E1EA1KF123456
```

---

## 4. Bot starten

```bash
python bot.py
```

Der Bot:

- fragt den Tesla-Status alle `TESLA_POLL_INTERVAL_SECONDS` (Standard: **90 Sekunden**) ab,
- benennt den Discord-Channel nur bei Änderung um (Cooldown bleibt bei 6 Min.),
- erkennt Fahrten für das Fahrtenbuch und schätzt kurze Fahrten zwischen zwei Abfragen.

### Slash-Command

```
/status
```

Postet den aktuellen Tesla-Status in den Channel – **ohne** den Channel umzubenennen.

---

## Projektstruktur

```
tesla-discord-status/
├── bot.py              # Discord-Bot, Background-Task, /status
├── config.py           # .env-Konfiguration
├── tesla_client.py     # TeslaPy-Anbindung + Fleet-API-Stub
├── status_mapper.py    # Status-Logik (Emoji, Akkustand)
├── channel_manager.py  # Channel-Rename + Cooldown
├── trip_tracker.py     # Fahrtenbuch-Erkennung
├── logging_setup.py    # Logging
├── requirements.txt
├── .env.example
└── README.md
```

---

## Konfiguration (.env)

| Variable | Pflicht | Beschreibung |
|----------|---------|--------------|
| `DISCORD_TOKEN` | Ja | Discord-Bot-Token |
| `CHANNEL_ID` | Ja | ID des Status-Channels |
| `TESLA_EMAIL` | Ja | Tesla-Account-E-Mail |
| `TESLA_VIN` | Nein | VIN bei mehreren Fahrzeugen |
| `TESLA_REFRESH_TOKEN` | Nein | Optionaler Refresh-Token |
| `TESLA_POLL_INTERVAL_SECONDS` | Nein | Tesla-Abfrageintervall (Standard: 90) |
| `CHANNEL_EDIT_COOLDOWN_MINUTES` | Nein | Min. Abstand zwischen Channel-Renames (Standard: 6) |
| `BATTERY_CACHE_MAX_AGE_SECONDS` | Nein | Max. Alter des Akku-Cache bei Sleep (Standard: 86400) |
| `TRIP_MIN_DISTANCE_KM` | Nein | Mindeststrecke für Fahrtenbuch (Standard: 0.3) |
| `LOG_LEVEL` | Nein | DEBUG, INFO, WARNING, … |
| `LOG_FILE` | Nein | Optional: Log-Dateipfad |

---

## Verhalten & Design-Entscheidungen

### Fahrzeug nicht unnötig wecken

1. Zuerst **Vehicle Summary** abrufen (weckt normalerweise nicht).
2. Bei `asleep` / `offline`: letzten bekannten Akkustand aus `battery_cache.json` (max. 24h alt).
3. Bei `online`: immer frische `vehicle_data` für Akku, Kilometerstand und Position.

### Fahrtenbuch

- Fahrten werden anhand des Status (🔵 unterwegs) erkannt.
- Kurze Fahrten zwischen zwei Abfragen werden per Kilometerstand-Delta geschätzt.
- Polling-Intervall für zuverlässiges Tracking: **60–120 Sekunden** empfohlen.

### Status-Logik

- `asleep` oder `offline` → ⚫
- `online` + (`shift_state` ist `None` oder `P` und Speed ≈ 0) → 🟢
- `online` + (`shift_state` in `D`/`R`/`N` oder Speed > 0) → 🔵

### Später: Tesla Fleet API

In `tesla_client.py` existiert ein `FleetApiClient`-Stub. Für MyTeslaMate, Teslemetry o. Ä. kann eine eigene Implementierung von `TeslaClientBase` eingehängt werden, ohne den Discord-Teil anzufassen.

---

## Fehlerbehebung

| Problem | Lösung |
|---------|--------|
| `Channel nicht gefunden` | `CHANNEL_ID` prüfen, Bot braucht Zugriff auf den Server |
| `Missing Access` beim Rename | Bot-Rolle braucht **Manage Channels**, Rolle muss über dem Channel liegen |
| Tesla Login: „Verified Successfully“ aber nichts passiert | URL aus Browser-Adresszeile ins **Terminal** einfügen (siehe oben) |
| `tesla_cache.json` fehlt | `python tesla_auth.py` ausführen und URL einfügen |
| Rate-Limit Discord | Cooldown erhöhen (`CHANNEL_EDIT_COOLDOWN_MINUTES=8`) |
| Akkustand bei Sleep = alt | Erwartet – Auto wird nicht geweckt; `(Cache)` im Log |
| Kurze Fahrten fehlen | `TESLA_POLL_INTERVAL_SECONDS=60` setzen |
| Falscher Akku online | Sollte behoben sein – bei Problemen `LOG_LEVEL=DEBUG` |

---

## Lizenz

MIT – frei verwendbar und anpassbar.
