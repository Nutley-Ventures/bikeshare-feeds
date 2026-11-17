#!/usr/bin/env python3
"""Pull bikeshare and bicycle-supply notices from TED (EU tenders) into tenders.json.

Uses the official TED Search API (https://api.ted.europa.eu/v3/notices/search):
free, no API key. Standard library only. Runs on GitHub Actions next to
poll_feeds.py, or by hand: python3 ted_tenders.py

Each search below runs on its own, so one rejected query doesn't stop the rest.
Notices are kept for 120 days and merged with the previous file, so the
database refresh can see what is new since its last run.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.ted.europa.eu/v3/notices/search"
OUT = os.environ.get("TENDERS_FILE", "tenders.json")
DAYS_BACK = int(os.environ.get("TED_DAYS_BACK", "21"))  # overlap so a missed week is caught
KEEP_DAYS = 120
UA = "bikeshare-feeds/1.0 (TED bikeshare and micromobility tender monitor)"

# Bicycles (34430000), non-motorised bicycles (34431000), bicycle parts (34432000).
CPV = "classification-cpv IN (34430000 34431000 34432000)"
# Phrases in the main EU languages for public bike sharing and its parts.
PHRASES = [
    # English
    "bike sharing", "bike-sharing", "bicycle sharing", "bikeshare", "cycle hire", "shared bicycles", "shared e-bikes",
    "bicycle rental system", "public bicycle",
    # French
    "vélos en libre-service", "vélo en libre-service", "vélos à assistance électrique", "vélos partagés",
    "location longue durée de vélos",
    # Spanish, Portuguese, Italian
    "bicicleta compartida", "bicicletas compartidas", "bicicleta pública", "bicicletas partilhadas",
    "bicicletas compartilhadas", "bike sharing comunale", "bicicletta condivisa", "biciclette condivise",
    # German, Dutch
    "Fahrradverleihsystem", "Bikesharing", "Leihfahrrad", "Fahrradverleih", "deelfietsen", "deelfiets",
    # Nordic, Baltic
    "kaupunkipyörä", "kaupunkipyörät", "bycykler", "bysykkel", "lånecyklar", "hyrcyklar", "rattaringlus",
    "velosipēdu koplietošana", "dviračių dalijimosi",
    # Central and Eastern Europe
    "rower miejski", "rowery miejskie", "sdílená kola", "sdílení jízdních kol", "zdieľané bicykle",
    "kerékpármegosztó", "izposoja koles", "javni bicikli", "dijeljenih bicikala",
    "biciclete partajate", "închiriere biciclete", "споделени велосипеди", "κοινόχρηστα ποδήλατα",
    # Shared e-scooters and mopeds
    "e-scooter sharing", "shared e-scooters", "shared scooters", "trottinettes en libre-service",
    "trottinettes électriques partagées", "scooters en libre-service", "patinetes compartidos",
    "patinetes eléctricos compartidos", "monopattini in sharing", "monopattini elettrici in sharing",
    "E-Scooter-Sharing", "E-Tretroller", "deelscooters", "deelsteps", "hulajnogi elektryczne",
    "sdílené koloběžky", "elsparkcyklar", "sähköpotkulaudat", "trotinete partilhadas",
    # Micromobility and mobility hubs
    "micromobility", "micromobilité", "micromovilidad", "micromobilità", "micromobilidade", "Mikromobilität",
    "mikromobilność", "mikromobilita", "shared mobility", "mobilité partagée", "movilidad compartida",
    "Mobilitätsstationen", "mobipunten", "mobiliteitshubs", "deelmobiliteit",
    # Bike subscription and long-term rental for residents
    "bike subscription", "e-bike subscription", "long-term e-bike rental", "location longue durée de vélos électriques",
    "location de vélos à assistance électrique", "location de vélos électriques", "abonnement vélo",
    "fietsabonnement", "alquiler de bicicletas eléctricas", "noleggio a lungo termine biciclette",
]
# Motor scooters and cycles with auxiliary motors (34420000, 34421000, 34422000): e-scooters, mopeds and some e-bikes.
CPV_MOTOR = "classification-cpv IN (34420000 34421000 34422000)"
FIELDS_FULL = ["publication-number", "publication-date", "notice-title", "notice-type", "buyer-name", "buyer-country",
               "classification-cpv", "estimated-value-proc", "estimated-value-cur-proc",
               "deadline-receipt-tender-date-lot", "place-of-performance"]
FIELDS_MIN = ["publication-number", "publication-date", "notice-title", "buyer-country", "classification-cpv",
              "estimated-value-proc", "estimated-value-cur-proc"]


def post(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def search(query, fields):
    """All notices for one query, following the iteration token. Returns (notices, error)."""
    out, token = [], None
    for _ in range(20):  # at most 20 pages of 100
        body = {"query": query, "fields": fields, "limit": 100, "paginationMode": "ITERATION"}
        if token:
            body["iterationNextToken"] = token
        try:
            res = post(body)
        except urllib.error.HTTPError as e:
            return out, f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
        except Exception as e:
            return out, f"{type(e).__name__}: {e}"[:200]
        page = res.get("notices") or []
        out += page
        token = res.get("iterationNextToken")
        if not token or len(page) < 100:
            break
        time.sleep(1)  # the API is throttled; be gentle
    return out, None


def first(v, lang=("eng", "fra", "deu", "spa", "ita", "nld", "por", "pol")):
    """Multilingual fields come back as {lang: text or [text]}; lists as lists."""
    if isinstance(v, dict):
        for k in lang:
            if v.get(k):
                return first(v[k])
        return first(next(iter(v.values()), None)) if v else None
    if isinstance(v, list):
        return first(v[0]) if v else None
    return v


def tidy(n):
    pn = str(n.get("publication-number") or "").strip()
    cpv = n.get("classification-cpv") or []
    cpv = sorted({str(c) for c in (cpv if isinstance(cpv, list) else [cpv])})[:8]
    val = first(n.get("estimated-value-proc"))
    try:
        val = float(val) if val not in (None, "") else None
    except (TypeError, ValueError):
        val = None
    dl = n.get("deadline-receipt-tender-date-lot")
    dl = sorted(str(x) for x in dl)[0] if isinstance(dl, list) and dl else (str(dl) if dl else None)
    return {k: v for k, v in {
        "pn": pn,
        "date": str(first(n.get("publication-date")) or "")[:10],
        "title": str(first(n.get("notice-title")) or "")[:300],
        "type": first(n.get("notice-type")),
        "buyer": str(first(n.get("buyer-name")) or "")[:160] or None,
        "cc": first(n.get("buyer-country")),
        "place": first(n.get("place-of-performance")),
        "cpv": cpv,
        "value": val,
        "cur": first(n.get("estimated-value-cur-proc")),
        "deadline": dl[:10] if dl else None,
        "url": f"https://ted.europa.eu/en/notice/-/detail/{pn}" if pn else None,
    }.items() if v not in (None, "", [])}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    since = (now.date() - dt.timedelta(days=DAYS_BACK)).strftime("%Y%m%d")
    date = f"publication-date>={since}"
    queries = [("cpv", f"{CPV} AND {date}"), ("cpv-motor", f"{CPV_MOTOR} AND {date}")] + [(p, f'FT~("{p}") AND {date}') for p in PHRASES]

    fields, found, errors = FIELDS_FULL, {}, {}
    probe, err = search(queries[0][1], fields)
    if err and "HTTP 400" in err:  # a field name the API doesn't know: fall back to the safe set
        fields = FIELDS_MIN
        probe, err = search(queries[0][1], fields)
    for label, q in queries:
        notices, err = (probe, err) if label == "cpv" else search(q, fields)
        if err:
            errors[label] = err
        for n in notices:
            t = tidy(n)
            if t.get("pn"):
                rec = found.setdefault(t["pn"], t)
                rec["hits"] = sorted(set(rec.get("hits", [])) | {label})  # which searches found it
        time.sleep(1)

    try:
        prev = {n["pn"]: n for n in json.load(open(OUT, encoding="utf-8")).get("notices", [])}
    except (OSError, ValueError, KeyError):
        prev = {}
    stamp = now.strftime("%Y-%m-%dT%H:%MZ")
    for pn, n in found.items():
        n["seen"] = prev.get(pn, {}).get("seen", stamp)  # when we first saw it
        prev[pn] = n
    oldest = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    keep = sorted((n for n in prev.values() if (n.get("date") or "9999") >= oldest), key=lambda n: n.get("date", ""), reverse=True)

    doc = {"updatedAt": stamp, "since": since, "queries": len(queries), "failed": errors, "fields": fields, "notices": keep}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"{len(found)} notices in the last {DAYS_BACK} days; {len(keep)} kept; {len(errors)} of {len(queries)} searches failed")
    for k, v in errors.items():
        print(f"  failed: {k}: {v}")
    if len(errors) == len(queries):
        sys.exit("Every TED search failed; check the network or the query syntax.")


if __name__ == "__main__":
    main()
