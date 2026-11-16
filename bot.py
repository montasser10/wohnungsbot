"""Wohnungsbot: sucht Mietwohnungen (Kleinanzeigen + WG-Gesucht) und schickt neue Treffer an Telegram.

Aufruf:
  python bot.py --dry-run     zeigt Treffer, sendet nichts, merkt sich nichts
  python bot.py --init        merkt sich alle aktuellen Anzeigen, ohne zu senden (erster Start)
  python bot.py               einmal suchen und neue Treffer senden
  python bot.py --loop 300    dauerhaft alle 300 s suchen
"""
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
SEEN = HERE / "seen.json"
ENV = HERE / ".env"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_env():
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def num(text):
    """'1.350 €' -> 1350.0, '2,5' -> 2.5"""
    if not text:
        return None
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


def get(url):
    r = S.get(url, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")


# ---------------------------------------------------------------- Portale

def kleinanzeigen(cfg):
    loc = cfg["kleinanzeigen_location_id"]
    radius = cfg["radius_km"]
    extra = cfg.get("kleinanzeigen_filter", "")
    articles = []
    for page in range(1, cfg.get("kleinanzeigen_pages", 1) + 1):
        seite = f"seite:{page}/" if page > 1 else ""
        path = cfg.get("kleinanzeigen_path", "s-wohnung-mieten")
        cat = cfg.get("kleinanzeigen_category", "c203")
        url = (f"https://www.kleinanzeigen.de/{path}/anzeige:angebote/"
               f"sortierung:neueste/{seite}{cat}l{loc}r{radius}{extra}")
        articles += get(url).select("article[data-adid]")
        time.sleep(1)
    out = []
    for a in articles:
        href = a.get("data-href") or ""
        img_el = a.select_one("img")
        image = img_el.get("src") if img_el else None
        ld_title = ""
        for sc in a.select('script[type="application/ld+json"]'):
            try:
                ld_title = json.loads(sc.string or "{}").get("title", "") or ld_title
            except ValueError:
                pass
        for t in a.select("script,svg,img"):
            t.decompose()
        parts = [p.strip() for p in a.get_text("|", strip=True).split("|")]
        text = " | ".join(parts)
        title = ld_title
        size = rooms = price = None
        place = ""
        for i, p in enumerate(parts):
            if re.match(r"^\d{5}\s", p) and not place:
                place = p
            m = re.search(r"([\d.,]+)\s*m²\s*·\s*([\d,]+)\s*Zi", p)
            if m:
                size, rooms = num(m.group(1)), num(m.group(2))
            elif size is None and re.fullmatch(r"[\d.,]+\s*m²", p):
                size = num(p)  # Gewerbe: nur Fläche, keine Zimmer
            if price is None and re.fullmatch(r"[\d.]+\s*€(\s*VB)?", p):
                price = num(p)
        if not title:
            # Titel ist der Teil nach "(x km)"
            for i, p in enumerate(parts):
                if re.fullmatch(r"\(\d+ km\)", p) and i + 1 < len(parts):
                    title = parts[i + 1]
                    break
        out.append({
            "id": "ka-" + a["data-adid"],
            "portal": "Kleinanzeigen",
            "title": title,
            "price": price,
            "size": size,
            "rooms": rooms,
            "place": place,
            "url": "https://www.kleinanzeigen.de" + href,
            "image": image,
            "text": text,
        })
    return out


def wggesucht(cfg):
    url = cfg["wggesucht_url"]
    out = []
    for c in get(url).select("[id^=liste-details-ad-]"):
        img_el = c.select_one("img[data-src]")
        image = img_el.get("data-src") if img_el else None
        title = c.get("aria-label", "")
        for t in c.select("img"):
            t.decompose()
        parts = [p.strip() for p in c.get_text("|", strip=True).split("|")]
        text = " | ".join(parts)
        price = size = rooms = None
        for p in parts:
            if price is None and re.fullmatch(r"\d+\s*€", p):
                price = num(p)
            elif size is None and re.fullmatch(r"\d+\s*m²", p):
                size = num(p)
            m = re.match(r"^(\d+(?:,\d)?)-Zimmer-Wohnung$", p)
            if m and rooms is None:
                rooms = num(m.group(1))
            if p == "1-Zimmer-Wohnung" or p.lower() == "1-zimmer-wohnung":
                rooms = 1.0
        street = ""
        if "location_on" in parts:
            i = parts.index("location_on")
            street = parts[i + 1] if i + 1 < len(parts) else ""
        out.append({
            "id": "wg-" + c.get("data-id", ""),
            "portal": "WG-Gesucht",
            "title": title,
            "price": price,
            "size": size,
            "rooms": rooms,
            "place": ("Gießen, " + street).strip(", "),
            "url": "https://www.wg-gesucht.de" + c.get("href", ""),
            "image": image,
            "text": text,
        })
    return out


def immowelt(cfg):
    """Seite 1 (neueste ~30) für Landkreis Gießen. Filter-URLs blockt Immowelt, darum lokal filtern."""
    out = []
    for c in get(cfg["immowelt_url"]).select('[data-testid="serp-core-classified-card-testid"]'):
        link = c.select_one('a[data-testid="card-mfe-covering-link-testid"]') or c.select_one("a[href]")
        if not link:
            continue
        href = link.get("href", "")
        info = link.get("title", "")  # "Wohnung zur Miete - Pohlheim - 350 € - 1 Zimmer, 25 m², ..."
        image = None
        for img in c.select("img"):
            src = img.get("src") or ""
            if "h=50" not in src and src.startswith("http"):
                image = src
                break
        text = c.get_text(" | ", strip=True)
        price_el = c.select_one('[data-testid="cardmfe-price-testid"]')
        price = num(price_el.get_text(" ", strip=True)) if price_el else None
        m_r = re.search(r"([\d,]+)\s*Zimmer", info)
        m_s = re.search(r"([\d.,]+)\s*m²", info)
        addr = re.search(r"\|\s*([^|]*\(\d{5}\))", text)
        bits = [b.strip() for b in info.split(" - ")]
        ort = bits[1] if len(bits) > 1 else ""
        if m_r:
            title = f"{m_r.group(1)}-Zimmer-Wohnung in {ort}".strip()
        else:  # Gewerbe, z. B. "Lager zur Miete - Pohlheim - ..."
            title = f"{bits[0]} in {ort}".strip() if bits else info
        out.append({
            "id": "iw-" + href.rstrip("/").split("/")[-1],
            "portal": "Immowelt",
            "title": title,
            "price": price,
            "size": num(m_s.group(1)) if m_s else None,
            "rooms": num(m_r.group(1)) if m_r else None,
            "place": addr.group(1).strip() if addr else "",
            "url": href if href.startswith("http") else "https://www.immowelt.de" + href,
            "image": image,
            "text": info + " | " + text,
        })
    return out


def ohnemakler(cfg):
    out = []
    for a in get(cfg["ohnemakler_url"]).select('a[href^="/immobilie/"]'):
        href = a["href"]
        if "list" in href:
            continue
        img = a.select_one("img")
        parts = [re.sub(r"\s+", " ", t).strip() for t in a.get_text("|", strip=True).split("|")]
        if len(parts) < 5:
            continue
        rooms = size = None
        for p in parts[3:6]:
            if re.fullmatch(r"[\d,]+\s*m²", p):
                size = num(p)
            elif re.fullmatch(r"\d+(,\d)?", p):
                rooms = num(p)
        out.append({
            "id": "om-" + href.strip("/").split("/")[-1],
            "portal": "Ohne-Makler",
            "title": parts[1],
            "price": num(parts[0]),
            "size": size,
            "rooms": rooms,
            "place": parts[2],
            "url": "https://www.ohne-makler.net" + href,
            "image": (img.get("src") or img.get("data-src")) if img else None,
            "text": " | ".join(parts),
        })
    return out


def _rooms_size_from_text(text):
    m_r = re.search(r"(\d+(?:[.,]5)?)\s*[- ]?(?:Zimmer|Zi\b|ZKB|Raum)", text, re.I)
    m_s = re.search(r"(\d{2,3}(?:[.,]\d+)?)\s*(?:m²|qm|m2)", text, re.I)
    return (num(m_r.group(1)) if m_r else None, num(m_s.group(1)) if m_s else None)


def marktde(cfg):
    out = []
    for it in get(cfg["marktde_url"]).select(".clsy-c-result-list-item"):
        a = it.select_one('a[href*="/a/"]')
        if not a:
            continue
        href = a["href"]
        img = it.select_one("img")
        parts = [re.sub(r"\s+", " ", t).strip() for t in it.get_text("|", strip=True).split("|")]
        text = " | ".join(parts)
        price = next((num(p) for p in parts if re.fullmatch(r"[\d.]+(,\d+)?\s*€", p)), None)
        place = next((p for p in parts if re.match(r"^\d{5}\s", p)), "")
        title = next((p for p in parts if len(p) > 25), "")
        rooms, size = _rooms_size_from_text(title + " " + text)
        lo, hi = cfg["filters"].get("min_rooms"), cfg["filters"].get("max_rooms")
        if size is None and rooms and (lo is None or rooms >= lo) and (hi is None or rooms <= hi):
            try:  # Fläche steht oft nur auf der Detailseite
                detail = get(href).get_text(" ", strip=True)
                m = re.search(r"Wohnfläche[^0-9]{0,20}(\d{2,3}(?:[.,]\d+)?)\s*m", detail) \
                    or re.search(r"(\d{2,3}(?:[.,]\d+)?)\s*(?:m²|qm)", detail)
                size = num(m.group(1)) if m else None
                time.sleep(1)
            except Exception:
                pass
        m_id = re.search(r"/a/([0-9a-f]+)", href)
        out.append({
            "id": "md-" + (m_id.group(1) if m_id else href),
            "portal": "markt.de",
            "title": title,
            "price": price,
            "size": size,
            "rooms": rooms,
            "place": place,
            "url": href.split("?")[0],
            "image": (img.get("src") or img.get("data-src")) if img else None,
            "text": text,
        })
    return out


def immobilo(cfg):
    out = []
    s = get(cfg["immobilo_url"])
    seen_href = set()
    for a in s.select('a[href^="/immobilien/"]'):
        href = a["href"]
        if not re.search(r"-[A-Z0-9]{6}$", href) or href in seen_href:
            continue
        seen_href.add(href)
        card = a
        for _ in range(6):
            if len(card.get_text(strip=True)) > 150:
                break
            card = card.parent
        img = card.select_one("img")
        parts = [re.sub(r"\s+", " ", t).strip() for t in card.get_text("|", strip=True).split("|")]
        text = " | ".join(parts)
        price = next((num(p) for p in parts if re.fullmatch(r"[\d.]+(,\d+)?\s*€", p)), None)
        rooms = next((num(p) for p in parts if re.fullmatch(r"[\d,]+\s*Zi\.", p)), None)
        size = None
        for i, p in enumerate(parts):
            if re.fullmatch(r"[\d.,]+\s*m", p):
                size = num(p)
                break
        place = next((p for p in parts if re.search(r"\b\d{5}\s", p)), "")
        out.append({
            "id": "io-" + href.rsplit("-", 1)[-1],
            "portal": "immobilo",
            "title": a.get_text(" ", strip=True) or next((p for p in parts if len(p) > 25), ""),
            "price": price,
            "size": size,
            "rooms": rooms,
            "place": place,
            "url": "https://www.immobilo.de" + href,
            "image": (img.get("src") or img.get("data-src")) if img else None,
            "text": text,
        })
    return out


def immoscout_mail(cfg):
    """Liest Suchauftrag-Mails von ImmoScout24 aus dem Postfach (IMAP, nur lesen, markiert nichts)."""
    import email
    import imaplib
    from email.header import decode_header, make_header

    user, pw = os.environ.get("IMAP_USER"), os.environ.get("IMAP_PASSWORD")
    if not (user and pw):
        raise RuntimeError("IMAP_USER / IMAP_PASSWORD fehlen in .env")
    host = os.environ.get("IMAP_HOST", "imap.gmail.com")
    days = cfg.get("immoscout_mail_days", 3)
    since = time.strftime("%d-%b-%Y", time.localtime(time.time() - days * 86400))

    out = []
    with imaplib.IMAP4_SSL(host) as M:
        M.login(user, pw)
        M.select("INBOX", readonly=True)
        _, data = M.search(None, f'(FROM "immobilienscout24" SINCE {since})')
        for num_id in data[0].split()[-30:]:
            _, msg_data = M.fetch(num_id, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            if re.search(r"kauf", subject, re.I):
                continue  # Kauf-Suchaufträge (Haus/ETW) gehören nicht zur Mietwohnungssuche
            html = None
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                    break
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            done = set()
            for a in soup.select("a[href]"):
                m = re.search(r"expose(?:/|%2F)(\d{6,})", a["href"])
                if not m or m.group(1) in done:
                    continue
                expose = m.group(1)
                # kleinster umgebender Block, der Preis + Fläche enthält
                block = a
                for _ in range(10):
                    t = block.get_text(" ", strip=True)
                    if "€" in t and re.search(r"m²|m2|qm", t):
                        break
                    block = block.parent
                    if block is None:
                        break
                if block is None:
                    continue
                if any(re.search(r"expose(?:/|%2F)(\d{6,})", x["href"]) and expose not in x["href"]
                       for x in block.select("a[href]")):
                    continue  # Block enthält mehrere Anzeigen -> zu groß
                done.add(expose)
                t = re.sub(r"\s+", " ", block.get_text(" | ", strip=True))
                price = next((num(x) for x in re.findall(r"([\d.]+(?:,\d+)?)\s*€", t)), None)
                m_s = re.search(r"([\d.,]+)\s*(?:m²|m2|qm)", t)
                m_r = re.search(r"([\d,]+)\s*(?:Zi\.?|Zimmer)", t)
                plz = re.search(r"\b(3\d{4})\b[^|]*", t)
                if not plz:
                    # Ort steht als eigener Teil nach den Zahlen, z. B. "Wieseck, Gießen"
                    loc = [p.strip() for p in t.split("|")
                           if "," in p and not re.search(r"\d", p) and len(p) < 60]
                    plz_place = loc[0] if loc else ""
                # Foto steht oft in einer Nachbarzelle: nach oben suchen, bis ein Anzeigenbild auftaucht,
                # aber nicht in einen Bereich mit einer anderen Anzeige hinein
                img, box = None, block
                for _ in range(6):
                    if box is None:
                        break
                    if any(re.search(r"expose(?:/|%2F)(\d{6,})", x["href"]) and expose not in x["href"]
                           for x in box.select("a[href]")):
                        break
                    img = next((i.get("src") for i in box.select("img")
                                if "pictures.immobilienscout24.de/listings" in (i.get("src") or "")), None)
                    if img:
                        break
                    box = box.parent
                title = max((x.strip() for x in t.split("|")), key=len)[:90]
                out.append({
                    "id": "is-" + expose,
                    "portal": "ImmoScout24",
                    "title": title,
                    "price": price,
                    "size": num(m_s.group(1)) if m_s else None,
                    "rooms": num(m_r.group(1)) if m_r else None,
                    "place": plz.group(0).strip() if plz else plz_place,
                    "url": f"https://www.immobilienscout24.de/expose/{expose}",
                    "image": img,
                    "text": t,
                })
    return out


PORTALS = {"kleinanzeigen": kleinanzeigen, "wggesucht": wggesucht,
           "immowelt": immowelt, "ohnemakler": ohnemakler,
           "marktde": marktde, "immobilo": immobilo,
           "immoscout_mail": immoscout_mail}


# ---------------------------------------------------------------- Preis-Leistung

MARKET = HERE / "market.json"


def ppsqm(ad):
    if ad["price"] and ad["size"]:
        return ad["price"] / ad["size"]
    return None


def update_market(ads, f):
    """Merkt sich €/m² aller passenden Anzeigen (letzte 400) als Marktvergleich."""
    market = json.loads(MARKET.read_text()) if MARKET.exists() else {}
    for a in ads:
        v = ppsqm(a)
        if v and f["min_eur_per_sqm"] <= v <= f["max_eur_per_sqm"]:
            first_seen = market[a["id"]][1] if a["id"] in market else int(time.time())
            market[a["id"]] = [round(v, 2), first_seen]  # Zeit bleibt gleich -> Datei ändert sich nur bei Neuem
    market = dict(sorted(market.items(), key=lambda kv: kv[1][1])[-400:])
    MARKET.write_text(json.dumps(market, indent=0, sort_keys=True))
    vals = sorted(v for v, _ in market.values())
    if len(vals) < f.get("min_market_samples", 0) and f.get("reference_eur_per_sqm"):
        return f["reference_eur_per_sqm"]  # zu wenig Vergleichsangebote: fester Richtwert
    return vals[len(vals) // 2] if vals else None


def rate(ad, median, f):
    """Gibt (ok, Text) zurück. ok = Anzeige ist günstiger als der Markt (je m²)."""
    v = ppsqm(ad)
    if v is None or median is None:
        return False, ""
    if not (f["min_eur_per_sqm"] <= v <= f["max_eur_per_sqm"]):
        return False, ""  # unplausibel (z. B. WG-Zimmerpreis oder Tippfehler)
    diff = (median - v) / median
    ad["value_diff"] = diff
    ad["ppsqm"] = v
    ad["median"] = median
    stars = "⭐⭐⭐" if diff >= 0.20 else "⭐⭐" if diff >= 0.10 else "⭐"
    eur = lambda x: f"{x:.2f}".replace(".", ",")
    text = (f"{stars} {eur(v)} €/m² · {diff*100:.0f}% unter Marktschnitt "
            f"(Ø {eur(median)} €/m², {f.get('market_label', '4 Zi. Gießen')})")
    return diff >= f["min_below_market"], text


# ---------------------------------------------------------------- Filter

def matches(ad, f):
    def within(v, lo, hi):
        if v is None:
            return lo is None and hi is None or not f.get("strict", False)
        return (lo is None or v >= lo) and (hi is None or v <= hi)

    if not within(ad["price"], f.get("min_rent"), f.get("max_rent")):
        return False
    if not within(ad["rooms"], f.get("min_rooms"), f.get("max_rooms")):
        return False
    if not within(ad["size"], f.get("min_size"), f.get("max_size")):
        return False
    plz = re.search(r"\b(3\d{4})\b", ad["place"])
    if plz and f.get("plz_whitelist") and plz.group(1) not in f["plz_whitelist"]:
        return False
    if plz and f.get("plz_prefixes") and not plz.group(1).startswith(tuple(f["plz_prefixes"])):
        return False
    if not plz and ad["place"] and f.get("town_whitelist"):
        place = ad["place"].lower()
        if not any(t.lower() in place for t in f["town_whitelist"]):
            return False  # Ort ohne PLZ (z. B. ImmoScout-Mail) außerhalb des Umkreises
    hay = (ad["title"] + " " + ad["text"]).lower()
    for ok_word in ("unmöbliert", "teilmöbliert", "nicht möbliert", "teilweise möbliert"):
        hay = hay.replace(ok_word, "")
    if any(w.lower() in hay for w in f.get("exclude_words", [])):
        return False
    req = f.get("require_any_words", [])
    if req and not any(w.lower() in hay for w in req):
        return False
    return True


# ---------------------------------------------------------------- Telegram

def fmt(v, unit):
    if v is None:
        return "?"
    s = f"{v:,.1f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {unit}".strip()


def caption(ad):
    from urllib.parse import quote
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    eur2 = lambda x: f"{x:.2f}".replace(".", ",")
    title = ad["title"] or f"{fmt(ad['rooms'], '')}-Zimmer-Wohnung"
    lines = []

    diff = ad.get("value_diff")
    if diff is not None:
        if diff >= 0.20:
            head = "⭐⭐⭐ <b>TOP-ANGEBOT</b>"
        elif diff >= 0.10:
            head = "⭐⭐ <b>GUTES ANGEBOT</b>"
        else:
            head = "⭐ <b>Angebot</b>"
        lines += [f"{head} · {diff*100:.0f} % unter Marktschnitt", ""]

    lines += [f"🏠 <b>{esc(title)}</b>", ""]
    rent_label = "Kaltmiete" if ad["portal"] == "Immowelt" else "Miete"
    lines.append(f"💶 {rent_label}:  <b>{fmt(ad['price'], '€')}</b>")
    lines.append(f"📐 Fläche:  {fmt(ad['size'], 'm²')}")
    if ad.get("rooms"):
        lines.append(f"🚪 Zimmer:  {fmt(ad['rooms'], '')}")
    if ad.get("ppsqm"):
        lines.append(f"📊 Preis/m²:  <b>{eur2(ad['ppsqm'])} €</b>  (Ø {eur2(ad['median'])} €)")
    if ad["place"]:
        maps = "https://www.google.com/maps/search/?api=1&query=" + quote(ad["place"])
        lines.append(f"📍 <a href=\"{maps}\">{esc(ad['place'])}</a>")
    lines += ["", f"👉 <a href=\"{ad['url']}\"><b>Anzeige öffnen</b></a>  ·  {ad['portal']}"]
    tag = "#top" if diff is not None and diff >= 0.20 else "#gut"
    lines.append(f"{tag} #{ad['portal'].lower().replace('-', '')}")
    return "\n".join(lines)


def send(ad):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    base = f"https://api.telegram.org/bot{token}"
    cap = caption(ad)
    if ad.get("image"):
        try:
            r = requests.post(f"{base}/sendPhoto", data={
                "chat_id": chat, "photo": ad["image"], "caption": cap, "parse_mode": "HTML"}, timeout=90)
            if r.ok:
                return
        except requests.exceptions.ReadTimeout:
            return  # Telegram lädt das Foto noch; die Nachricht kommt meist trotzdem an, kein Duplikat senden
    r = requests.post(f"{base}/sendMessage", data={
        "chat_id": chat, "text": cap, "parse_mode": "HTML",
        "disable_web_page_preview": "true"}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram: {r.status_code} {r.text[:200]}")


# ---------------------------------------------------------------- Ablauf

def fingerprint(ad):
    plz = re.search(r"\b(3\d{4})\b", ad["place"])
    if not (ad["price"] and ad["size"] and plz):
        return None
    return f"fp:{plz.group(1)}-{round(ad['price'])}-{round(ad['size'])}"


def run_once(cfg, mode):
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    found = []
    for name in cfg["portals"]:
        try:
            ads = PORTALS[name](cfg)
            log(f"{name}: {len(ads)} Anzeigen")
            found += ads
        except Exception as e:
            log(f"{name}: Fehler {e}")
    f = cfg["filters"]
    found = list({a["id"]: a for a in found}.values())  # TOP-Anzeigen stehen auf mehreren Seiten
    # Gleiche Wohnung auf mehreren Portalen (z. B. immobilo sammelt fremde Anzeigen): nur einmal
    uniq, fps = [], set()
    for a in found:
        fp = fingerprint(a)
        if fp and fp in fps:
            continue
        if fp:
            fps.add(fp)
        a["fp"] = fp
        uniq.append(a)
    found = uniq
    candidates = [a for a in found if matches(a, f)]
    median = update_market(candidates, f)
    log(f"Marktschnitt: {median} €/m² ({len(candidates)} passende Anzeigen)")
    hits = []
    for a in candidates:
        ok, a["value_text"] = rate(a, median, f)
        if ok:
            hits.append(a)
    hits.sort(key=lambda a: -a.get("value_diff", 0))
    new = [a for a in hits if a["id"] not in seen and not (a.get("fp") and a["fp"] in seen)]
    log(f"{len(hits)} mit guter Preis-Leistung, {len(new)} neu")

    if mode == "summary":
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        counts = {}
        for a in found:
            counts[a["portal"]] = counts.get(a["portal"], 0) + 1
        lines = [f"🔎 <b>Testlauf {time.strftime('%d.%m. %H:%M')}</b>",
                 "Geladen: " + ", ".join(f"{k} {v}" for k, v in counts.items()),
                 f"4-Zimmer im Umkreis: {len(candidates)} · Ø {fmt(median, '€/m²')}",
                 f"Gute Preis-Leistung (≥{f['min_below_market']*100:.0f}% unter Ø): <b>{len(hits)}</b>", ""]
        for i, a in enumerate(hits, 1):
            title = a["title"] or f"{fmt(a['rooms'], '')}-Zimmer-Wohnung"
            lines.append(f"{i}. <a href=\"{a['url']}\">{esc(title[:55])}</a>\n"
                         f"    {fmt(a['price'], '€')} · {fmt(a['size'], 'm²')} · "
                         f"{fmt(ppsqm(a), '€/m²')} · {esc(a['place'])}")
        if not hits:
            lines.append("Gerade kein Angebot unter dem Marktschnitt.")
        r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                          data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": "\n".join(lines),
                                "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=30)
        log("Zusammenfassung gesendet" if r.ok else f"Fehler: {r.text[:200]}")
        return

    if mode == "dry":
        for a in candidates:
            mark = "SENDEN" if a in hits else "zu teuer/unklar"
            print("  ", mark, "|", a["portal"], "|", fmt(a["price"], "€"), fmt(a["size"], "m²"),
                  fmt(a["rooms"], "Zi."), "|", a.get("value_text") or "-", "|", a["title"][:50])
        return
    if mode == "init":
        seen |= {a["id"] for a in found}
    else:
        for a in reversed(new):  # bestes Angebot landet unten (zuletzt) im Kanal
            try:
                send(a)
                seen.add(a["id"])
                if a.get("fp"):
                    seen.add(a["fp"])
                log("gesendet:", a["title"][:60])
                time.sleep(1.5)
            except Exception as e:
                log("Senden fehlgeschlagen:", e)
    SEEN.write_text(json.dumps(sorted(seen)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--summary", action="store_true", help="Testlauf als eine Übersicht in den Kanal")
    ap.add_argument("--loop", type=int, default=0, help="Sekunden zwischen Suchläufen")
    ap.add_argument("--check-mail", action="store_true", help="nur ImmoScout24-Mails lesen und anzeigen")
    ap.add_argument("--scheduled", action="store_true", help="Einzellauf per Zeitplan (beachtet die Nachtpause)")
    ap.add_argument("--profile", default="", help="z. B. 'lager' -> config_lager.json, seen_lager.json ...")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    global CONFIG, SEEN, MARKET
    if args.profile:
        CONFIG = HERE / f"config_{args.profile}.json"
        SEEN = HERE / f"seen_{args.profile}.json"
        MARKET = HERE / f"market_{args.profile}.json"
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if cfg.get("chat_env"):  # eigener Telegram-Kanal für dieses Profil
        os.environ["TELEGRAM_CHAT_ID"] = os.environ.get(cfg["chat_env"], "")
    if args.check_mail:
        ads = immoscout_mail(cfg)
        print(f"{len(ads)} Anzeigen aus ImmoScout24-Mails")
        for a in ads:
            print(" ", a["id"], fmt(a["price"], "€"), fmt(a["size"], "m²"), fmt(a["rooms"], "Zi."),
                  "|", a["place"], "|", a["title"][:60])
        return
    # ImmoScout24-Mails nur mitlesen, wenn Zugangsdaten eingetragen sind
    if not (os.environ.get("IMAP_USER") and os.environ.get("IMAP_PASSWORD")):
        cfg["portals"] = [p for p in cfg["portals"] if p != "immoscout_mail"]
    mode = ("dry" if args.dry_run else "init" if args.init
            else "summary" if args.summary else "send")
    if mode in ("send", "summary") and not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        sys.exit("TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID fehlen in wohnungsbot/.env")
    def berlin_hour():
        try:  # Server (z. B. GitHub) laufen in UTC, die Nachtpause gilt in deutscher Zeit
            from datetime import datetime
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo("Europe/Berlin")).hour
        except Exception:
            return time.localtime().tm_hour  # Windows ohne tzdata: PC-Zeit

    def quiet_now():
        q = cfg.get("quiet_hours")  # z. B. [23, 6] = Pause von 23:00 bis 06:00
        if not q or not (args.loop or args.scheduled):
            return False
        h = berlin_hour()
        start, end = q
        return (h >= start or h < end) if start > end else (start <= h < end)

    if args.scheduled and quiet_now():
        log("Nachtpause, kein Suchlauf")
        return

    paused = False
    while True:
        if quiet_now():
            if not paused:
                log(f"Nachtpause bis {cfg['quiet_hours'][1]:02d}:00")
                paused = True
            time.sleep(60)
            continue
        if paused:
            log("Nachtpause vorbei, suche weiter")
            paused = False
        run_once(cfg, mode)
        if not args.loop:
            break
        time.sleep(args.loop + random.randint(0, 60))


if __name__ == "__main__":
    main()
