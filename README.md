# Wohnungsbot Gießen

Der Bot sucht Mietwohnungen auf **Kleinanzeigen** (Gießen + 10 km), **Immowelt** (Landkreis Gießen, neueste 30), **Ohne-Makler**, **markt.de**, **immobilo** (sammelt auch Anzeigen anderer Seiten) und **WG-Gesucht** (Gießen). Er schickt jede neue passende Anzeige einmal in deinen Telegram-Kanal. Dieselbe Wohnung auf mehreren Portalen kommt nur einmal. Orte außerhalb von ca. 10 km sortiert er über `plz_whitelist` aus. Von 23 bis 6 Uhr pausiert er (`quiet_hours`).

## Einrichten (einmalig)
1. In Telegram **@BotFather** öffnen, `/newbot` schicken und den Token kopieren.
2. Einen Kanal erstellen und den Bot dort als **Administrator** hinzufügen (Recht: Nachrichten senden).
3. `.env.example` nach `.env` kopieren, dann Token und Kanal (`@kanalname`) eintragen.
4. In `config.json` unter `filters` deine Kriterien eintragen (`null` = egal):
   - `max_rent`: höchste Miete in €
   - `min_rooms` / `max_rooms`: Zimmerzahl, von–bis
   - `min_size` / `max_size`: Wohnfläche in m², von–bis
   - `exclude_words`: Wörter, die eine Anzeige aussortieren
   - `strict: true`: Anzeigen ohne Preis-, Zimmer- oder Flächenangabe werden verworfen

## Preis-Leistung (aktuell: 4 bis 4,5 Zimmer)
Für jede 4-Zimmer-Anzeige rechnet der Bot den Preis pro m² aus und vergleicht ihn mit dem Marktschnitt aller 4-Zimmer-Anzeigen, die er gesehen hat (gespeichert in `market.json`). Gesendet wird eine Anzeige nur, wenn sie mindestens 10 % günstiger als dieser Schnitt ist (`min_below_market` in `config.json`).
- ⭐⭐⭐ = mindestens 20 % günstiger
- ⭐⭐ = mindestens 10 % günstiger

Anzeigen ohne Preis- oder Flächenangabe und Werte unter 5 €/m² oder über 25 €/m² sortiert der Bot aus. Das sind meist WG-Zimmerpreise oder Tippfehler.

## Starten
- Doppelklick auf `start.bat`. Beim ersten Start merkt sich der Bot alle aktuellen Anzeigen, ohne sie zu senden. Danach sucht er alle 5 bis 6 Minuten und schickt nur neue Anzeigen.
- Nur testen, ohne etwas zu senden: `python bot.py --dry-run`
- Der Bot läuft nur, solange der PC an ist und das Fenster offen bleibt.

## Hinweise
- ImmoScout24, meinestadt.de und Wohnungsbörse (leitet zu ImmoScout24 um) blockieren automatische Abfragen. Wohnbau Gießen hat keine Online-Angebotsliste, dort registrierst du dich als Wohnungssuchender.

## Zweite Suche: Lager (Profil `lager`)
- Lagerflächen und Hallen **zur Miete**, **150–300 m²**, Gießen ±20 km, auf Kleinanzeigen (Gewerbeimmobilien), Immowelt (Lager) und immobilo (Gewerbe).
- Einstellungen stehen in `config_lager.json`, gesendet wird in den Kanal **„Lager Gießen“** (`TELEGRAM_CHAT_ID_LAGER` in `.env`).
- Preis-Leistung: Solange der Bot weniger als 8 Vergleichsangebote gesehen hat, rechnet er mit einem Richtwert von 6 €/m² (`reference_eur_per_sqm`), danach mit dem echten Schnitt.
- Von Hand starten: `python bot.py --profile lager --dry-run` bzw. `--summary`
- `start.bat` startet beide Suchen, die Lager-Suche läuft in einem eigenen minimierten Fenster.

## Läuft auf GitHub (seit 16.11.2026)
- Beide Suchen laufen über GitHub Actions (`.github/workflows/bot.yml`), durchgehend alle ca. 5 Minuten (Nachtpause 23–6 Uhr). Jeder Lauf dauert ca. 5,5 Stunden und startet danach selbst den nächsten; der Zeitplan alle 3 Stunden ist nur Ersatz. Der PC kann aus sein. Das Repo ist öffentlich (unbegrenzte Laufzeit), Zugangsdaten bleiben als Secrets geheim.
- Zugangsdaten liegen als verschlüsselte Secrets im Repo. Neu setzen: `gh secret set -f .env --repo montasser10/wohnungsbot`
- Die Listen der schon gesendeten Anzeigen (`seen*.json`, `market*.json`) speichert der Lauf selbst ins Repo.
- Sofort einen Lauf starten: `gh workflow run bot.yml --repo montasser10/wohnungsbot`
- Pausieren: `gh workflow disable bot.yml --repo montasser10/wohnungsbot` (weiter mit `enable`)
- **`start.bat` nicht parallel benutzen**, sonst kommen Anzeigen doppelt. Vor Änderungen an Dateien erst `git pull`, danach `git push`.
