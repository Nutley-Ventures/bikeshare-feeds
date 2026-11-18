#!/usr/bin/env python3
"""Past bikeshare tenders and awards from TED (EU), back to 2016, into desk/tender_history.json.

The weekly ted_tenders.py only keeps the last 120 days. This one looks back years so the
database can show every past tender against its system, and so we can review what cities
asked for (fleet size, e-bikes, docks, IoT, contract length) and who won.

Official TED Search API (https://api.ted.europa.eu/v3/notices/search): free, no key.
Standard library only; honest user agent. Run by .github/workflows/history.yml.
"""
import datetime as dt
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request

API = "https://api.ted.europa.eu/v3/notices/search"
OUT = "desk/tender_history.json"
FROM = os.environ.get("HISTORY_FROM", "2016")
UA = "bikeshare-feeds/1.0 (history of public bikeshare tenders for market research; github.com/Nutley-Ventures/bikeshare-feeds)"

# Public bike sharing only (no staff leasing, no e-scooters): the phrases from ted_tenders.py.
PHRASES = [
    "bike sharing", "bike-sharing", "bicycle sharing", "bikeshare", "cycle hire", "shared bicycles", "shared e-bikes",
    "bicycle rental system", "public bicycle", "public bike",
    "vélos en libre-service", "vélo en libre-service", "vélos partagés", "vélopartage", "vélos à assistance électrique en libre-service",
    "bicicleta compartida", "bicicletas compartidas", "bicicleta pública", "bicicletas públicas", "bicicletas partilhadas",
    "bicicletas compartilhadas", "bike sharing comunale", "bicicletta condivisa", "biciclette condivise", "bicicletas de uso público",
    "Fahrradverleihsystem", "Bikesharing", "Leihfahrrad", "Leihräder", "Fahrradverleih", "Mietradsystem", "deelfietsen", "deelfiets",
    "kaupunkipyörä", "kaupunkipyörät", "bycykler", "bysykkel", "bysykler", "lånecyklar", "hyrcyklar", "rattaringlus",
    "velosipēdu koplietošana", "dviračių dalijimosi", "rower miejski", "rowery miejskie", "roweru miejskiego", "sdílená kola",
    "sdílení jízdních kol", "zdieľané bicykle", "kerékpármegosztó", "közbringa", "izposoja koles", "javni bicikli",
    "dijeljenih bicikala", "biciclete partajate", "închiriere biciclete", "споделени велосипеди", "κοινόχρηστα ποδήλατα",
]
CPV = "classification-cpv IN (34430000 34431000 34432000 34420000 34421000)"
# A bicycle-supply notice only counts if its text points to a public sharing scheme.
SHARE = re.compile("|".join([
    r"shar", r"hire", r"rental", r"public bike", r"public bicycle", r"docking", r"\bdocks?\b", r"stations?\b",
    r"libre[- ]service", r"partag", r"compartid", r"partilhad", r"compartilhad", r"condivis", r"publica",
    r"verleih", r"leihrad", r"mietrad", r"deelfiets", r"kaupunkipyor", r"bycyk", r"bysykk", r"lanecyk", r"hyrcyk",
    r"rower(y|u)? miejsk", r"sdilen", r"zdielan", r"kerekparmegoszt", r"kozbring", r"izposoj", r"javni bicikl", r"partajat",
]))
NOT_SHARE = re.compile(r"leasing|lease|dienstrad|jobrad|fietsplan|formansc|police|polic[ií]a|polizei|gendarm|military|army|armee|scooter sharing|trottinette|patinete|monopattin")

BASE = ["publication-number", "publication-date", "notice-title", "notice-type", "buyer-name", "buyer-country",
        "classification-cpv", "estimated-value-proc", "estimated-value-cur-proc"]
# Richer fields, tried one by one: the API rejects names it doesn't know, so only accepted ones are used.
EXTRA = ["description-lot", "description-proc", "title-lot", "title-proc", "buyer-city", "place-of-performance",
         "place-of-performance-city-lot", "deadline-receipt-tender-date-lot", "contract-nature", "procedure-type",
         "winner-name", "winner-country", "organisation-name-tenderer", "tender-value", "tender-value-cur",
         "result-value-lot", "result-value-cur-lot", "total-value", "total-value-cur", "estimated-value-lot",
         "duration-period-value-lot", "duration-period-unit-lot", "contract-duration-period-lot", "framework-agreement-lot",
         # how bids are scored (eForms, from late 2023): criterion type, name, description and weight
         "award-criterion-type-lot", "award-criterion-name-lot", "award-criterion-description-lot", "award-criterion-number-lot",
         "award-criterion-number-weight-lot", "award-criterion-number-fixed-lot", "award-criterion-number-threshold-lot",
         "award-criteria-order-justification-lot", "award-criterion-type-part", "award-criterion-name-part", "award-criterion-number-part",
         # where the tender documents are (eForms BT-15 and BT-615)
         "document-url-lot", "document-url-part", "document-restricted-url-lot", "document-restricted-url-part"]
CRIT = [f for f in EXTRA if "award-criteri" in f]


def post(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def search(query, fields, pages=20):
    out, token = [], None
    for _ in range(pages):
        body = {"query": query, "fields": fields, "limit": 100, "paginationMode": "ITERATION"}
        if token:
            body["iterationNextToken"] = token
        for attempt in range(3):
            try:
                res = post(body)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                return out, f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
            except Exception as e:
                if attempt < 2:
                    time.sleep(10)
                    continue
                return out, f"{type(e).__name__}: {e}"[:200]
        page = res.get("notices") or []
        out += page
        token = res.get("iterationNextToken")
        if not token or len(page) < 100:
            break
        time.sleep(1.2)
    return out, None


def first(v, lang=("eng", "fra", "deu", "spa", "ita", "nld", "por", "pol", "ces", "hun", "ron", "slk", "slv", "hrv", "fin", "swe", "dan", "est", "lav", "lit", "bul", "ell")):
    if isinstance(v, dict):
        for k in lang:
            if v.get(k):
                return first(v[k])
        return first(next(iter(v.values()), None)) if v else None
    if isinstance(v, list):
        return first(v[0]) if v else None
    return v


def texts(v):
    """Every text of a multilingual field, English first, joined (descriptions can be split per lot)."""
    if isinstance(v, dict):
        k = next((k for k in ("eng", "fra", "deu", "spa", "ita", "nld", "por", "pol") if v.get(k)), next(iter(v), None))
        return texts(v[k]) if k else ""
    if isinstance(v, list):
        return " | ".join(t for t in (texts(x) for x in v) if t)
    return str(v) if v not in (None, "") else ""


def num(v):
    v = first(v)
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def norm(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def tidy(n, label):
    pn = str(n.get("publication-number") or "").strip()
    cpv = n.get("classification-cpv") or []
    cpv = sorted({str(c) for c in (cpv if isinstance(cpv, list) else [cpv])})[:8]
    dl = n.get("deadline-receipt-tender-date-lot")
    dl = sorted(str(x) for x in dl)[0] if isinstance(dl, list) and dl else (str(dl) if dl else None)
    desc = texts(n.get("description-lot")) or texts(n.get("description-proc"))
    winners = n.get("winner-name") or n.get("organisation-name-tenderer")
    win = []
    for w in (winners if isinstance(winners, list) else [winners]):
        t = first(w) if isinstance(w, dict) else w
        if t and str(t) not in win:
            win.append(str(t)[:120])
    rec = {
        "pn": pn,
        "date": str(first(n.get("publication-date")) or "")[:10],
        "title": str(first(n.get("notice-title")) or "")[:300],
        "type": first(n.get("notice-type")),
        "buyer": str(first(n.get("buyer-name")) or "")[:160] or None,
        "city": first(n.get("buyer-city")) or first(n.get("place-of-performance-city-lot")),
        "cc": first(n.get("buyer-country")),
        "place": first(n.get("place-of-performance")),
        "cpv": cpv,
        "value": num(n.get("estimated-value-proc")) or num(n.get("estimated-value-lot")),
        "cur": first(n.get("estimated-value-cur-proc")),
        "award": num(n.get("total-value")) or num(n.get("result-value-lot")) or num(n.get("tender-value")),
        "awardCur": first(n.get("total-value-cur")) or first(n.get("result-value-cur-lot")) or first(n.get("tender-value-cur")),
        "winners": win[:5],
        "duration": " ".join(str(first(n.get(k)) or "") for k in ("duration-period-value-lot", "duration-period-unit-lot")).strip() or first(n.get("contract-duration-period-lot")),
        "framework": first(n.get("framework-agreement-lot")),
        "nature": first(n.get("contract-nature")),
        "procedure": first(n.get("procedure-type")),
        "deadline": dl[:10] if dl else None,
        "desc": re.sub(r"\s+", " ", desc).strip()[:2500],
        "url": f"https://ted.europa.eu/en/notice/-/detail/{pn}" if pn else None,
        "hits": [label],
        "docs": first(n.get("document-url-lot")) or first(n.get("document-url-part")),
        "docsLogin": first(n.get("document-restricted-url-lot")) or first(n.get("document-restricted-url-part")),
        "crit": {k.replace("award-criterion-", "").replace("award-criteria-", ""): n.get(k) for k in CRIT if n.get(k) not in (None, "", [], {})} or None,
    }
    return {k: v for k, v in rec.items() if v not in (None, "", [])}


def main():
    os.makedirs("desk", exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    start = f"{FROM}0101"
    # 1. which rich fields does the API accept?
    fields = list(BASE)
    for f in EXTRA:
        _, err = search(f'FT~("bike sharing") AND publication-date>={now.year - 1}0101', BASE + [f], pages=1)
        if not err:
            fields.append(f)
        time.sleep(0.6)
    print("fields used:", fields, flush=True)
    sample, _ = search('FT~("bike sharing") AND publication-date>=20250101', fields, pages=1)
    shown = 0
    for n in sample:  # show how the scoring fields come back, to check the parser
        c = {k: n.get(k) for k in CRIT if n.get(k)}
        if c and shown < 3:
            print("criteria sample", n.get("publication-number"), json.dumps(c, ensure_ascii=False)[:1500], flush=True)
            shown += 1

    found, failed = {}, {}

    def add(notices, label, need_share):
        kept = 0
        for n in notices:
            t = tidy(n, label)
            if not t.get("pn"):
                continue
            blob = norm(" ".join([t.get("title", ""), t.get("desc", "")]))
            if NOT_SHARE.search(blob) and not re.search(r"bike ?shar|bicycle shar|libre-service|kaupunkipyor|rower miejski|deelfiets", blob):
                continue
            if need_share and not SHARE.search(blob):
                continue
            r = found.setdefault(t["pn"], t)
            r["hits"] = sorted(set(r.get("hits", [])) | {label})
            kept += 1
        return kept

    # 2. phrase searches over the whole period; split by year if a search hits the page cap
    for p in PHRASES:
        q = f'FT~("{p}") AND publication-date>={start}'
        res, err = search(q, fields)
        if err:
            failed[p] = err
        if len(res) >= 2000:
            res = []
            for y in range(int(FROM), now.year + 1):
                r2, e2 = search(f'FT~("{p}") AND publication-date>={y}0101 AND publication-date<{y + 1}0101', fields)
                res += r2
                if e2:
                    failed[f"{p} {y}"] = e2
        print(f"{p}: {len(res)} notices, kept {add(res, p, False)}", flush=True)
        time.sleep(1)
    # 3. bicycle CPV codes year by year, kept only when the text points to a sharing scheme
    for y in range(int(FROM), now.year + 1):
        res, err = search(f"{CPV} AND publication-date>={y}0101 AND publication-date<{y + 1}0101", fields)
        if err:
            failed[f"cpv {y}"] = err
        print(f"cpv {y}: {len(res)} notices, kept {add(res, 'cpv', True)}", flush=True)
        time.sleep(1)

    keep = sorted(found.values(), key=lambda n: n.get("date", ""), reverse=True)
    doc = {"updatedAt": now.strftime("%Y-%m-%dT%H:%MZ"), "from": FROM, "fields": fields, "failed": failed, "notices": keep}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=0)
    years = {}
    for n in keep:
        years[n.get("date", "")[:4]] = years.get(n.get("date", "")[:4], 0) + 1
    print(f"{len(keep)} past bikeshare notices since {FROM}; by year {dict(sorted(years.items()))}; {len(failed)} searches failed")


if __name__ == "__main__":
    main()
