#!/usr/bin/env python3
"""Fetch free public reference data for the desktop study into desk/.

- Wikipedia's "List of bicycle-sharing systems" (wikitext, CC BY-SA): city, name,
  hardware system, operator, launch year, stations and bikes for hundreds of systems.
- Wikidata (CC0): bike-sharing systems with operator, manufacturer, start date,
  location, country, website and English Wikipedia article.

Standard library only; honest user agent; runs on GitHub Actions (desk.yml).
"""
import json
import os
import time
import urllib.parse
import urllib.request

UA = "bikeshare-feeds/1.0 (desktop study of public bike-sharing systems; https://github.com/Nutley-Ventures/bikeshare-feeds)"
OUT = "desk"
PAGES = {
    "en": ["List of bicycle-sharing systems"],
}


def get(url, data=None, accept="application/json"):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def wikitext(lang, title):
    q = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "wikitext|revid", "redirects": 1, "format": "json", "formatversion": 2})
    j = json.loads(get(f"https://{lang}.wikipedia.org/w/api.php?{q}"))
    if "error" in j:
        raise RuntimeError(j["error"].get("info", "error"))
    return {"lang": lang, "title": j["parse"]["title"], "revid": j["parse"].get("revid"), "wikitext": j["parse"]["wikitext"]}


def qid_for(term):
    q = urllib.parse.urlencode({"action": "wbsearchentities", "search": term, "language": "en", "type": "item", "limit": 5, "format": "json"})
    j = json.loads(get(f"https://www.wikidata.org/w/api.php?{q}"))
    return [(x["id"], x.get("label"), x.get("description")) for x in j.get("search", [])]


SPARQL = """
SELECT ?item ?itemLabel ?countryCode ?placeLabel ?operatorLabel ?makerLabel ?start ?end ?site ?article ?coord WHERE {
  VALUES ?cls { %s }
  ?item wdt:P31/wdt:P279* ?cls .
  OPTIONAL { ?item wdt:P17 ?country . ?country wdt:P297 ?countryCode . }
  OPTIONAL { ?item wdt:P131 ?place . }
  OPTIONAL { ?item wdt:P137 ?operator . }
  OPTIONAL { ?item wdt:P176 ?maker . }
  OPTIONAL { ?item wdt:P571 ?start . }
  OPTIONAL { ?item wdt:P576 ?end . }
  OPTIONAL { ?item wdt:P856 ?site . }
  OPTIONAL { ?item wdt:P625 ?coord . }
  OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,de,fr,es,it,nl,pl,pt,cs" . }
}
"""


def wikidata(qids):
    body = urllib.parse.urlencode({"query": SPARQL % " ".join("wd:" + q for q in qids), "format": "json"}).encode()
    j = json.loads(get("https://query.wikidata.org/sparql", data=body, accept="application/sparql-results+json"))
    rows = []
    for b in j["results"]["bindings"]:
        rows.append({k: v.get("value") for k, v in b.items()})
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    status = {"updatedAt": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "sources": {}}
    for lang, titles in PAGES.items():
        for t in titles:
            name = f"wikipedia-{lang}-" + t.lower().replace(" ", "-")
            try:
                doc = wikitext(lang, t)
                json.dump(doc, open(f"{OUT}/{name}.json", "w", encoding="utf-8"), ensure_ascii=False)
                status["sources"][name] = {"ok": True, "chars": len(doc["wikitext"]), "revid": doc["revid"]}
            except Exception as e:
                status["sources"][name] = {"ok": False, "error": str(e)[:200]}
            time.sleep(2)
    try:
        cands = qid_for("bicycle-sharing system") + qid_for("bike sharing system")
        status["classes"] = cands
        qids = sorted({c[0] for c in cands if c[1] and "shar" in c[1].lower()})[:4]
        rows = wikidata(qids)
        json.dump({"classes": qids, "rows": rows}, open(f"{OUT}/wikidata.json", "w", encoding="utf-8"), ensure_ascii=False)
        status["sources"]["wikidata"] = {"ok": True, "rows": len(rows), "classes": qids}
    except Exception as e:
        status["sources"]["wikidata"] = {"ok": False, "error": str(e)[:300]}
    json.dump(status, open(f"{OUT}/status.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(status, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
