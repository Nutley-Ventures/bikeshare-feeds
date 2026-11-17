#!/usr/bin/env python3
"""Pull bikeshare, micromobility and bike-leasing tenders from free national tender portals.

Complements ted_tenders.py (EU-wide notices from TED). Each source below is free and
needs no key. Every source runs on its own: if one portal is down or changes its
format, the others still run, and the failure is recorded in world_tenders.json.

Sources
  uk-fts   UK Find a Tender (OCDS API)
  uk-cf    UK Contracts Finder (OCDS search)
  (ca-buys CanadaBuys open tender notices: refuses automated downloads, not used)
  ca-seao  Quebec SEAO weekly OCDS files (province, cities, agencies)
  br-pncp  Brazil national procurement portal (PNCP)
  co-secop Colombia SECOP II (datos.gov.co)
  fr-boamp France BOAMP (below and above EU thresholds)
  de-doe   Germany Datenservice Oeffentlicher Einkauf (daily OCDS export)
  es-place Spain Plataforma de Contratacion del Sector Publico (ATOM feed)

Standard library only. Run: python3 world_tenders.py
"""
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

OUT = os.environ.get("WORLD_FILE", "world_tenders.json")
DAYS_BACK = int(os.environ.get("WORLD_DAYS_BACK", "21"))
KEEP_DAYS = 120
MAX_PER_SOURCE = 300
SOURCE_BUDGET = int(os.environ.get("WORLD_SOURCE_SECS", "150"))  # each portal gets this long, then we move on
_t0 = [time.time()]


class OutOfTime(Exception):
    pass


LIMITS = {"es-place": 420, "uk-fts": 420, "br-pncp": 420}  # slower portals get longer
_cur = [""]


def budget():
    lim = LIMITS.get(_cur[0], SOURCE_BUDGET)
    if time.time() - _t0[0] > lim:
        raise OutOfTime(f"stopped after {lim}s")
UA = "bikeshare-feeds/1.0 (bikeshare and micromobility tender monitor; github.com/Nutley-Ventures/bikeshare-feeds)"

NOW = dt.datetime.now(dt.timezone.utc)
SINCE = NOW.date() - dt.timedelta(days=DAYS_BACK)

# Words that make a notice worth a look (matched on lower-case text with accents removed).
# Loose on purpose: the database runs judge each notice by its title afterwards.
KEYWORDS = [
    r"bike ?shar", r"bikeshar", r"bicycle shar", r"\bbikes?\b", r"\bbicycles?\b", r"\be-?bikes?\b", r"cycle hire",
    r"cycle to work", r"cycle scheme", r"\bscooters?\b", r"micro-?mobilit", r"mobility hub",
    r"\bvelos?\b", r"libre-service", r"trottinette", r"\bbicicleta", r"\bbicicletta", r"\bbiciclette\b", r"\bbici\b",
    r"patinete", r"monopattin", r"fahrrad", r"pedelec", r"dienstrad", r"jobrad", r"lastenrad",
    r"\bfiets(en|lease|leasing|plan|abonnement)?\b", r"deelfiets", r"deelscooter", r"deelmobiliteit",
    r"\bcykl", r"\bcykel", r"\bsykkel", r"\browery?\b", r"rower miejski", r"sdilen", r"\bkola\b",
    r"micromovilidad", r"micromobilidade", r"bicicletas? compartilhad", r"bicicletas? compartid", r"bicicletas? publicas?",
]
KW = re.compile("|".join(KEYWORDS))


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


def hit(*texts):
    return bool(KW.search(norm(" ".join(t for t in texts if t))))


def get(url, accept="application/json", data=None, timeout=60):
    budget()
    headers = {"User-Agent": UA, "Accept": accept}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url, **kw):
    return json.loads(get(url, **kw).decode("utf-8-sig"))


def day(s):
    """First YYYY-MM-DD in a string, or ''."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(s or ""))  # dd/mm/yyyy
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def num(v):
    try:
        f = float(str(v).replace(",", "").strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


PARTIAL = []
STATE = {}  # per-source cursors kept between runs (e.g. the next page to read)


def rec(src, nid, date, title, url, **kw):
    r = {"src": src, "id": f"{src}:{nid}", "date": day(date), "title": re.sub(r"\s+", " ", str(title or "")).strip()[:300], "url": url}
    for k, v in kw.items():
        if v not in (None, "", []):
            r[k] = (re.sub(r"\s+", " ", v).strip()[:400] if isinstance(v, str) else v)
    if r.get("deadline"):
        r["deadline"] = day(r["deadline"]) or None
    r = {k: v for k, v in r.items() if v not in (None, "", [])}
    PARTIAL.append(r)
    return r


# ---------- OCDS helpers (UK, Quebec, Germany) ----------
def from_release(src, rel, cc, url_fn):
    t = rel.get("tender") or {}
    title = t.get("title") or rel.get("title") or ""
    desc = t.get("description") or ""
    if not hit(title, desc):
        return None
    buyer = (rel.get("buyer") or {}).get("name") or next((p.get("name") for p in rel.get("parties", []) if "buyer" in (p.get("roles") or [])), "")
    val = (t.get("value") or {})
    awards = rel.get("awards") or []
    if not val.get("amount") and awards:
        val = awards[0].get("value") or {}
    stage = "award" if awards else ("planning" if "planning" in (rel.get("tag") or []) else "tender")
    return rec(src, rel.get("ocid") or rel.get("id"), rel.get("date") or (t.get("tenderPeriod") or {}).get("startDate"), title,
               url_fn(rel), buyer=buyer, cc=cc, value=num(val.get("amount")), cur=val.get("currency"),
               deadline=(t.get("tenderPeriod") or {}).get("endDate"), type=stage, text=desc[:300])


def src_uk_fts():
    since = NOW - dt.timedelta(days=3)  # runs daily, so three days overlap is plenty
    out, url = [], ("https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=100&updatedFrom="
                    + since.strftime("%Y-%m-%dT%H:%M:%S"))
    for i in range(150):
        t1 = time.time()
        pkg = get_json(url, timeout=200)
        if i == 0:
            print(f"  uk-fts first page in {round(time.time() - t1)}s, releases: {len(pkg.get('releases', []))}", flush=True)
        for rel in pkg.get("releases", []):
            r = from_release("uk-fts", rel, "GBR", lambda x: f"https://www.find-tender.service.gov.uk/Notice/{x.get('id')}")
            if r:
                out.append(r)
        nxt = (pkg.get("links") or {}).get("next")
        if not nxt:
            cur = pkg.get("cursor") or (pkg.get("links") or {}).get("cursor")
            nxt = (url.split("&cursor=")[0] + "&cursor=" + urllib.parse.quote(cur)) if cur else None
        if not nxt or not pkg.get("releases"):
            break
        url = nxt
        time.sleep(0.5)
    return out


def src_uk_cf():
    out = []
    url = ("https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?limit=100&publishedFrom="
           + SINCE.strftime("%Y-%m-%dT00:00:00") + "&publishedTo=" + NOW.strftime("%Y-%m-%dT%H:%M:%S"))
    for i in range(150):
        pkg = get_json(url)
        if i == 0:
            print("  uk-cf first page:", {k: (len(v) if isinstance(v, list) else v) for k, v in pkg.items() if k != "releases"}, "releases:", len(pkg.get("releases", [])), flush=True)
        for rel in pkg.get("releases", []):
            r = from_release("uk-cf", rel, "GBR", lambda x: "https://www.contractsfinder.service.gov.uk/Notice/" + re.sub(r"^ocds-[a-z0-9]+-", "", str(x.get("ocid") or x.get("id") or "")))
            if r:
                out.append(r)
        nxt = (pkg.get("links") or {}).get("next")
        if not nxt or not pkg.get("releases"):
            break
        url = nxt
        time.sleep(0.5)
    return out


def src_ca_buys():
    raw = get("https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv", accept="text/csv, */*", timeout=180)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "replace"))))
    if not rows:
        return []
    cols = list(rows[0].keys())
    pick = lambda *keys: next((c for c in cols if all(k in c.lower() for k in keys)), None)
    c_title, c_desc = pick("title", "eng"), pick("description", "eng")
    c_ref, c_pub, c_close = pick("referencenumber") or pick("reference"), pick("publicationdate") or pick("publication"), pick("closing")
    c_buyer, c_url = pick("contractingentityname", "eng") or pick("entity", "eng"), pick("noticeurl", "eng") or pick("url", "eng")
    out = []
    for r in rows:
        title, desc = r.get(c_title, ""), r.get(c_desc, "") if c_desc else ""
        if not hit(title, desc[:2000]):
            continue
        out.append(rec("ca-buys", r.get(c_ref) or title[:40], r.get(c_pub), title, r.get(c_url) or "https://canadabuys.canada.ca/en/tender-opportunities",
                       buyer=r.get(c_buyer), cc="CAN", deadline=r.get(c_close), type="tender", text=desc[:300]))
    if not out and rows:
        print("  ca-buys columns:", cols[:40])
    return out


def src_ca_seao():
    pkg = get_json("https://www.donneesquebec.ca/recherche/api/3/action/package_show?id=systeme-electronique-dappel-doffres-seao")
    res = [r for r in pkg["result"]["resources"] if r.get("url", "").lower().endswith(".json")]
    res.sort(key=lambda r: r.get("last_modified") or r.get("created") or "", reverse=True)
    print("  ca-seao files:", [(r.get("name"), (r.get("last_modified") or r.get("created") or "")[:10]) for r in res[:4]], flush=True)
    out = []
    for r in res[:2]:  # the latest weekly files
        data = get_json(r["url"], timeout=300)
        rl = data.get("releases") or []
        print("  ca-seao", r.get("name"), "releases:", len(rl), flush=True)
        if rl:
            s = rl[0]
            print("  ca-seao sample:", json.dumps({"keys": list(s)[:15], "tender": {k: (str(v)[:80]) for k, v in (s.get("tender") or {}).items()}}, ensure_ascii=False)[:900], flush=True)
        rels = data.get("releases") or [rl for p in data.get("records", []) for rl in ([p.get("compiledRelease")] if p.get("compiledRelease") else [])]
        for rel in rels:
            x = from_release("ca-seao", rel, "CAN", lambda z: "https://seao.gouv.qc.ca/")
            if x and (x.get("date") or "9999") >= SINCE.isoformat():
                out.append(x)
    return out


def src_br_pncp():
    """Official PNCP consulta API: tenders currently open for proposals, filtered by our keywords."""
    out, seen = [], set()
    end = (NOW.date() + dt.timedelta(days=120)).strftime("%Y%m%d")
    st = STATE.setdefault("br-pncp", {})
    start = int(st.get("next_page") or 1)
    for page in list(range(start, 700)) + list(range(1, start)):
        st["next_page"] = page  # where to pick up if we run out of time
        url = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta?" + urllib.parse.urlencode({"dataFinal": end, "pagina": page, "tamanhoPagina": 50})
        data = None
        for attempt in range(2):
            try:
                data = get_json(url, timeout=90)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise OutOfTime("rate-limited; will continue from page %d next run" % page)
                if e.code >= 500 and attempt == 0:
                    time.sleep(5)
                    continue
                if e.code >= 500:
                    break  # skip a page the portal can't serve
                raise
        if data is None:
            continue
        items = data.get("data") or []
        if page == 1:
            print("  br-pncp pages:", data.get("totalPaginas"), "records:", data.get("totalRegistros"), flush=True)
        for it in items:
            obj = it.get("objetoCompra") or ""
            if not hit(obj, it.get("informacaoComplementar") or ""):
                continue
            org, uni = it.get("orgaoEntidade") or {}, it.get("unidadeOrgao") or {}
            key = it.get("numeroControlePNCP") or obj[:60]
            if key in seen:
                continue
            seen.add(key)
            cnpj, ano, seq = org.get("cnpj"), it.get("anoCompra"), it.get("sequencialCompra")
            out.append(rec("br-pncp", key, it.get("dataPublicacaoPncp"), obj,
                           f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}" if cnpj and ano and seq else "https://pncp.gov.br/app/editais",
                           buyer=org.get("razaoSocial"), cc="BRA", place=", ".join(x for x in [uni.get("municipioNome"), uni.get("ufSigla")] if x),
                           value=num(it.get("valorTotalEstimado")), cur="BRL", deadline=it.get("dataEncerramentoProposta"), type="tender"))
        if not items or page >= (data.get("totalPaginas") or 0):
            st["next_page"] = 1
            if start == 1:
                break
            continue
        time.sleep(1.5)  # the portal rate-limits; stay well under it
    return out


def src_co_secop():
    out, seen = [], set()
    for q in ["bicicletas", "bicicleta", "patinetas", "micromovilidad", "bicicletas compartidas"]:
        url = "https://www.datos.gov.co/resource/p6dx-8zbt.json?" + urllib.parse.urlencode(
            {"$q": q, "$limit": 200, "$order": ":updated_at DESC"})
        rows = get_json(url)
        for r in rows:
            d = next((r[k] for k in r if k.startswith("fecha_de_publicacion")), "") or r.get("fecha_de_ultima_publicaci", "")
            if day(d) and day(d) < SINCE.isoformat():
                continue
            title = r.get("nombre_del_procedimiento") or ""
            desc = r.get("descripci_n_del_procedimiento") or ""
            if not hit(title, desc):
                continue
            key = r.get("id_del_proceso") or r.get("referencia_del_proceso") or title[:60]
            if key in seen:
                continue
            seen.add(key)
            link = r.get("urlproceso")
            link = link.get("url") if isinstance(link, dict) else link
            out.append(rec("co-secop", key, d, title or desc[:200], link or "https://community.secop.gov.co/",
                           buyer=r.get("entidad"), cc="COL", place=", ".join(x for x in [r.get("ciudad_entidad"), r.get("departamento_entidad")] if x),
                           value=num(r.get("precio_base")), cur="COP", type=r.get("estado_del_procedimiento") or "tender", text=desc[:300]))
        time.sleep(1)
    if not out and rows:
        print("  co-secop fields:", sorted(rows[0].keys())[:60])
    return out


def src_fr_boamp():
    out, seen = [], set()
    base = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
    for q in ["vélos", "vélo", "trottinettes", "micromobilité", "vélos à assistance électrique", "location longue durée"]:
        where = f'search(objet, "{q}") and dateparution >= date\'{SINCE.isoformat()}\''
        data = get_json(base + "?" + urllib.parse.urlencode({"where": where, "limit": 100, "order_by": "dateparution desc"}))
        for r in data.get("results", []):
            idweb = r.get("idweb") or r.get("id")
            if idweb in seen or not hit(r.get("objet")):
                continue
            seen.add(idweb)
            nature = r.get("nature_libelle") or r.get("nature") or ""
            out.append(rec("fr-boamp", idweb, r.get("dateparution"), r.get("objet"),
                           r.get("url_avis") or f"https://www.boamp.fr/pages/avis/?q=idweb:{idweb}",
                           buyer=r.get("nomacheteur"), cc="FRA", deadline=r.get("datelimitereponse"),
                           type="award" if "attribution" in norm(nature) else "tender", place=", ".join(r.get("code_departement") or []) if isinstance(r.get("code_departement"), list) else r.get("code_departement")))
        time.sleep(1)
    return out


def src_de_doe():
    out = []
    for i in range(min(DAYS_BACK, 7)):  # daily exports for the last week
        d = (NOW.date() - dt.timedelta(days=i + 1)).isoformat()
        try:
            raw = get(f"https://oeffentlichevergabe.de/api/notice-exports?pubDay={d}&format=ocds.zip", accept="application/zip", timeout=300)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                continue
            raise
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for name in z.namelist():
                if not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(z.read(name).decode("utf-8-sig"))
                except ValueError:
                    continue
                rels = data.get("releases") or ([data] if data.get("ocid") else [])
                for rel in rels:
                    nid = str(rel.get("id") or rel.get("ocid") or "")
                    x = from_release("de-doe", rel, "DEU", lambda z, n=nid: f"https://oeffentlichevergabe.de/ui/de/search/details?noticeId={n}")
                    if x:
                        out.append(x)
        time.sleep(1)
    return out


def src_es_place():
    ns = {"a": "http://www.w3.org/2005/Atom"}
    url = "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom"
    out = []
    for _ in range(8):  # each page holds the most recent notices; walk back until older than SINCE
        root = ET.fromstring(get(url, accept="application/atom+xml", timeout=180))
        oldest = None
        for e in root.findall("a:entry", ns):
            upd = (e.findtext("a:updated", "", ns) or "")[:10]
            oldest = upd if oldest is None or upd < oldest else oldest
            title, summ = e.findtext("a:title", "", ns), e.findtext("a:summary", "", ns)
            if not hit(title, summ):
                continue
            link = next((l.get("href") for l in e.findall("a:link", ns) if l.get("href")), "")
            m = re.search(r"Importe:\s*([\d.,]+)\s*([A-Z]{3})?", summ or "")
            out.append(rec("es-place", e.findtext("a:id", "", ns), upd, title, link, cc="ESP",
                           value=num(m.group(1).replace(".", "").replace(",", ".")) if m else None, cur=(m.group(2) if m and m.group(2) else "EUR") if m else None,
                           buyer=(re.search(r"Órgano de Contratación:\s*([^;]+)", summ or "") or [None, None])[1], type="tender", text=summ[:300]))
        nxt = next((l.get("href") for l in root.findall("a:link", ns) if l.get("rel") == "next"), None)
        if not nxt or (oldest and oldest < SINCE.isoformat()):
            break
        url = nxt
        time.sleep(1)
    return out


# CanadaBuys refuses automated downloads (HTTP 403), so it is left out; Canadian federal tenders are covered by the web searches.
SOURCES = [("uk-fts", src_uk_fts), ("uk-cf", src_uk_cf), ("ca-seao", src_ca_seao),
           ("br-pncp", src_br_pncp), ("co-secop", src_co_secop), ("fr-boamp", src_fr_boamp), ("de-doe", src_de_doe),
           ("es-place", src_es_place)]


def main():
    only = set(sys.argv[1:])
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    notices = {n["id"]: n for n in prev.get("notices", [])}
    STATE.update(prev.get("state") or {})
    status = dict(prev.get("sources", {}))
    stamp = NOW.strftime("%Y-%m-%dT%H:%MZ")
    for name, fn in SOURCES:
        if only and name not in only:
            continue
        t0 = time.time(); _t0[0] = t0; _cur[0] = name; PARTIAL.clear()
        print(f"{name}: starting", flush=True)
        try:
            found = fn()[:MAX_PER_SOURCE]
            status[name] = {"ok": True, "found": len(found), "at": stamp, "secs": round(time.time() - t0)}
            for n in found:
                n["seen"] = notices.get(n["id"], {}).get("seen", stamp)
                notices[n["id"]] = n
            print(f"{name}: {len(found)} matching notices ({round(time.time() - t0)}s)", flush=True)
        except Exception as e:  # one portal failing must not stop the rest
            msg = f"{type(e).__name__}: {e}"[:300]
            part = PARTIAL[:MAX_PER_SOURCE]
            status[name] = {"ok": False, "error": msg, "at": stamp, "found": len(part)}
            for n in part:
                n["seen"] = notices.get(n["id"], {}).get("seen", stamp)
                notices[n["id"]] = n
            print(f"{name}: FAILED {msg} (kept {len(part)} found before stopping)", flush=True)
    oldest = (NOW.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    keep = sorted((n for n in notices.values() if (n.get("date") or "9999") >= oldest), key=lambda n: n.get("date", ""), reverse=True)
    doc = {"updatedAt": stamp, "since": SINCE.isoformat(), "sources": status, "state": STATE, "notices": keep}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"{len(keep)} notices kept; {sum(1 for s in status.values() if s.get('ok'))} of {len(status)} sources working")


if __name__ == "__main__":
    main()
