"""Turn desk/tender_history.json into database docs (db/tenders/meta.json, hist-NN.json) with
hardware facts read from the notice text: fleet size, e-bikes, docked or free floating, stations,
docks, contract years, supply or operations, cargo bikes, IoT, batteries, solar, adaptive bikes.
The Monday database refresh copies db/tenders/ into the "tenders" collection.
usage: python3 tender_db.py [desk/tender_history.json] [db/tenders]"""
import json, os, re, sys, unicodedata

def norm(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()

NUM = r"(\d{1,3}(?:[ ., ]\d{3})+|\d{1,5})"
BIKE = r"(?:new\s+|nuevas?\s+|nouveaux\s+|nuove\s+|neue\s+|nowych\s+)?(?:(?:e|electric|electrically assisted|pedal[- ]assist(?:ed)?)[- ]?)?(?:bikes?|bicycles?|cycles?|velos?|bicicletas?|biciclette|bicicli|fahrrader|rader|pedelecs?|e-?bikes?|rowerow|rowery|rowerami|kol\b|jizdnich kol|bicyklov|kerekpar\w*|polkupyor\w*|cyklar|cykler|sykler|fietsen|bicikal\w*|bicikl\w*|biciclete|podilat\w*)"
STAT = r"(?:docking stations?|stations?|estaciones|estacions|stazioni|stationen|stacji|stacje|stanic|stanice|allomas\w*|asemaa|asemat|stationer|postaj\w*|estacoes|estacoes|bornes?)"
DOCK = r"(?:docks?|docking points?|bike stands?|points? d'attache|anclajes|stalli|stellplatze|stojak\w*|bornettes?)"
YEARS = r"(?:years?|ans|anos|anni|jahre[n]?|lat|let|let\b|ev\w*|vuotta|ar\b|jaar|godin\w*|ani\b|χρονια)"
EB = re.compile(r"\be-?bikes?\b|electric (?:bike|bicycle|assist)|pedal[- ]assist|pedelec|assistance electrique|\bvae\b|electrica|elettric|elektro|elektryczn|elektrick|elektromos|sahko|el-?cykl|elsykk|elektrische|elektricn|electrice|electricas|ebike")
MECH = re.compile(r"mechanical|mecanique|mecanic|convencional|muscular|tradizional|conventional bikes?|klassische|tradycyjn|klasick|normal bikes?|non-electric|pedal bikes?")
FREE = re.compile(r"dockless|free[- ]floating|sans station|flotante|flusso libero|stationslos|free float|freefloat|bez stacji|bez stanic")
VIRT = re.compile(r"virtual station|geofenc|virtuelle station|station virtuelle|estaciones virtuales|stazioni virtuali|wirtualn")
HYB = re.compile(r"hybrid|hybride|hibrid|ibrid")
DOCKW = re.compile(r"docking|\bdocks?\b|station[- ]based|bornes|anclaje|stalli|stojak|dokovac|stellplatz|ancoragem|terminal")
SUPPLY = re.compile(r"\bsupply|deliver|purchase|procure|acquisition|fourniture|achat|suministro|adquisicion|compra|fornitura|acquisto|lieferung|beschaffung|kauf|dostaw|zakup|dodavk|nakup|szallit|beszerz|hankin|leverans|levering|nabav|furnizar|aquisic|προμηθει")
OPER = re.compile(r"operat|exploitation|gestion|gestione|betrieb|betreib|obslug|provoz|uzemelt|palvelu|drift|exploatar|exploracao|upravljan|servizio di|service public|maintenance and operation|management of the")
CONC = re.compile(r"concession|concesion|concessione|konzession|koncesj|koncesi|koncesszi|concessao")
CARGO = re.compile(r"cargo bikes?|cargo-?bikes?|velos? cargo|lastenrad|lastenfahr|bicicletas? de carga|bakfiets|rower\w* cargo|nakladn\w* kol")
IOT = re.compile(r"\biot\b|\bgps\b|\bgsm\b|telemat|smart lock|connected lock|cadenas connecte|candado inteligente|tracking|geolocali|lokaliz|\bnfc\b|bluetooth|\blte\b|\bsim\b")
SWAP = re.compile(r"(removable|swappable|interchangeable|replaceable) batter|batterie amovible|bateria extraible|batteria estraibile|wechselakku|herausnehmbar|battery swap|akumulator\w* wymienn")
DOCKCH = re.compile(r"charg\w* (?:at|in|on) (?:the )?(?:dock|station)|recharge en station|carga en (?:la )?estacion|ricarica (?:nelle|alle) stazion|ladestation|charging station|charging dock|stazioni di ricarica|estaciones de carga")
SOLAR = re.compile(r"solar|solaire|fotovolta|photovolta|photovolt")
ADAPT = re.compile(r"adaptive|handbike|tricycle|accessible bikes?|inclusive cycl|velos adaptes|bicicletas adaptadas|bici adattat")

def tonum(s):
    s = re.sub(r"[ ., ](?=\d{3}\b)", "", s)
    try:
        return int(s)
    except ValueError:
        return None

def biggest(pat, text, lo, hi):
    best = None
    for m in re.finditer(NUM + r"\s*(?:x\s*)?" + pat, text):
        n = tonum(m.group(1))
        if n and lo <= n <= hi and (best is None or n > best):
            best = n
    return best

def facts(title, desc, dur):
    t = norm(title + " . " + desc)
    x = {}
    f = biggest(BIKE, t, 5, 25000)
    if f and not re.search(r"\b(19|20)\d\d\b", str(f)):
        x["fleet"] = f
    s = biggest(STAT, t, 2, 3000)
    if s:
        x["stations"] = s
    d = biggest(DOCK, t, 5, 60000)
    if d:
        x["docks"] = d
    e, mch = bool(EB.search(t)), bool(MECH.search(t))
    if e:
        x["ebike"] = "some" if mch else "all"
    elif mch:
        x["ebike"] = "none"
    if FREE.search(t):
        x["infra"] = "Free floating"
    elif VIRT.search(t):
        x["infra"] = "Virtual stations"
    elif HYB.search(t) and DOCKW.search(t):
        x["infra"] = "Hybrid"
    elif DOCKW.search(t) or s:
        x["infra"] = "Docked"
    y = None
    m = re.search(r"(\d{1,2})\s*" + YEARS + r"\b", t)
    if m and 1 <= int(m.group(1)) <= 15 and re.search(r"contract|period|duration|durata|duree|duracion|laufzeit|okres|doba|kesto|varaktighet|looptijd|trajanje|durata|for a|por un|pour une|per un", t[max(0, m.start() - 80):m.start()]):
        y = int(m.group(1))
    if dur:
        dm = re.match(r"(\d+(?:\.\d+)?)\s*(\w+)", str(dur))
        if dm:
            v, u = float(dm.group(1)), dm.group(2).lower()
            yy = v / 12 if u.startswith("mon") else v / 365 if u.startswith("day") else v
            if 0.5 <= yy <= 20:
                y = round(yy, 1) if yy % 1 else int(yy)
    if y:
        x["years"] = y
    sp, op, co = bool(SUPPLY.search(t)), bool(OPER.search(t)), bool(CONC.search(t))
    x["scope"] = "concession" if co else "full" if sp and op else "supply" if sp else "operation" if op else ""
    if not x["scope"]:
        del x["scope"]
    if CARGO.search(t): x["cargo"] = True
    if IOT.search(t): x["iot"] = True
    if SWAP.search(t): x["battery"] = "Swappable"
    elif DOCKCH.search(t) and e: x["battery"] = "Charged in the dock"
    if SOLAR.search(t): x["solar"] = True
    if ADAPT.search(t): x["adaptive"] = True
    return x

BIKEW = re.compile(r"bike|bicycl|\bcycles?\b|cycle hire|velo|bicicl|biciclet|fahrrad|\brad\b|leihrad|mietrad|rower|\bkol[ao]?\b|jizdni|bicykl|kerekpar|bringa|polkupyor|kaupunkipyor|cykel|cykl|sykkel|fiets|bicikl|podilat|pedelec|e-?bike")
SHAREW = re.compile(r"shar|hire|libre[- ]service|partag|compartid|partilhad|compartilhad|condivis|public[ao]s?\b|publique|verleih|leihrad|mietrad|deelfiets|kaupunkipyor|bycyk|bysykk|lanecyk|miejsk|sdilen|zdielan|megoszt|kozbring|izposoj|javni|partajat|inchiriere|koinoxrist|public bike|city ?bike|stacj|station|docking")
WIDER_CPV = ("45", "71", "79", "73", "77", "66", "70", "85", "90", "92", "98")  # works, engineering, advertising, research, insurance...
LANG_ONLY = {"közbringa": ("HUN",), "κοινόχρηστα ποδήλατα": ("GRC", "CYP"), "rower miejski": ("POL",), "rowery miejskie": ("POL",), "roweru miejskiego": ("POL",)}


def relevance(n):
    """core: a bikeshare tender; wider: bikeshare is one part of a bigger project (roadworks, design, advertising); drop: a search artefact."""
    hits = set(n.get("hits", []))
    phrase_hits = hits - {"cpv"}
    if phrase_hits and all(p in LANG_ONLY and n.get("cc") not in LANG_ONLY[p] for p in phrase_hits):
        return "drop"  # a phrase from another country's language matched unrelated words
    t = norm(n.get("title", "") + " " + n.get("desc", ""))
    cpv = n.get("cpv", [])
    bike_cpv = any(c.startswith(("3443", "3442")) for c in cpv)
    if bike_cpv or (BIKEW.search(t) and SHAREW.search(t)):
        return "core"
    if cpv and all(c.startswith(WIDER_CPV) for c in cpv):
        return "wider"
    return "core"


CRIT_THEMES = [
    ("warranty", r"warrant|guarant|garanti|gwaranc|zaruk|garanzia|takuu|jamstv|garancij|gewahrleist"),
    ("delivery", r"czas uruchom|start of service|launch time|implementation time|deliver|livraison|delai|entrega|plazo|consegna|liefer|dostaw|dodani|termen de livrare|lead time|time of delivery|toimitus|leverans|rok isporuk|isporuk"),
    ("battery", r"batter|baterij|autonom|range|reichweite|akku|akumul|charging|recharg|punjen"),
    ("service", r"mainten|servic|response time|reaction|entretien|mantenim|manutenz|wartung|serwis|udrzb|odrzav|vzdrzev|repair|repar|availability|disponib|uptime|intervention|interventi"),
    ("env", r"environment|co2|emission|sustain|environnement|ambiental|ambientale|umwelt|recycl|ekolog|ecolog|carbon|circular|klima|green|energ|okolis|okolj"),
    ("experience", r"experien|team|personnel|staff|manager|methodolog|project plan|organi[sz]ation|qualif|esperienza|erfahrung|doswiadcz|zkusen|referenc|kompeten|expert|iskustv|izkusn"),
    ("social", r"social|local|employment|apprentic|insertion|inclus|accessib|socijal"),
    ("quality", r"technic|techniq|tecnic|technisch|techniczn|tehnic|quality|qualite|calidad|qualita|qualitat|jakosc|kvalit|kakovost|design|weight|ergonom|function|specif|solution|propos|concept|merit|valeur technique|tehnick"),
]


def criteria(c):
    """Award criteria from eForms fields: [{th: theme, name, w: weight number or None, kind: per-exa/poi-exa/ord-imp, d: short description}]"""
    if not c:
        return None
    def lst(v):
        if isinstance(v, dict):
            for k in ("eng", "fra", "deu", "spa", "ita", "nld", "por", "pol"):
                if v.get(k):
                    return lst(v[k])
            return lst(next(iter(v.values()))) if v else []
        return v if isinstance(v, list) else ([v] if v not in (None, "") else [])
    types, names, descs = lst(c.get("type-lot")), lst(c.get("name-lot")), lst(c.get("description-lot"))
    nums, kinds = lst(c.get("number-lot")), lst(c.get("number-weight-lot"))
    n = max(len(types), len(names), len(nums))
    out = []
    for i in range(n):
        ty = str(types[i] if i < len(types) else "").lower()
        nm = str(names[i] if i < len(names) else "").strip()
        ds = str(descs[i] if i < len(descs) else "").strip()
        try:
            w = float(nums[i]) if i < len(nums) and nums[i] not in (None, "") else None
        except (TypeError, ValueError):
            w = None
        if w is not None and w == int(w):
            w = int(w)
        th = "price" if ty in ("price", "cost") else (next((k for k, p in CRIT_THEMES if re.search(p, norm(nm))), None) if nm else None) or next((k for k, p in CRIT_THEMES if re.search(p, norm(ds))), "other")
        if ty not in ("price", "cost") and re.search(r"\b(price|prix|precio|prezzo|preis|cena|pret|hinta|pris)\b", norm(nm)):
            th = "price"
        out.append({k: v for k, v in {"th": th, "name": nm[:90], "w": w, "kind": str(kinds[i]) if i < len(kinds) else None, "d": ds[:220] if ds and ds != nm else None}.items() if v not in (None, "")})
    return out or None


TYPES = {"cn": "tender", "pin": "planned", "pmc": "planned", "qu": "planned", "can": "award", "veat": "change", "corr": "change"}


h = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "desk/tender_history.json", encoding="utf-8"))
out = sys.argv[2] if len(sys.argv) > 2 else "db/tenders"; os.makedirs(out, exist_ok=True)
for f in os.listdir(out):
    if f.startswith("hist-"):
        os.remove(os.path.join(out, f))  # a smaller history must not leave old parts behind
rows = []
for n in h["notices"]:
    rel = relevance(n)
    if rel == "drop":
        continue
    x = facts(n.get("title", ""), n.get("desc", ""), n.get("duration"))
    r = {"id": n["pn"], "d": n.get("date"), "t": n.get("title"), "ty": n.get("type"), "b": n.get("buyer"), "city": n.get("city"), "cc": n.get("cc"), "v": n.get("value"), "cur": n.get("cur"),
         "aw": n.get("award"), "awc": n.get("awardCur"), "w": n.get("winners"), "dur": n.get("duration"), "dl": n.get("deadline"), "url": n.get("url"), "desc": (n.get("desc") or "")[:1200], "x": x, "k": "wider" if rel == "wider" else None, "doc": n.get("docs") or n.get("docsLogin"), "cr": criteria(n.get("crit"))}
    rows.append({k: v for k, v in r.items() if v not in (None, "", [], {})})
parts, cur, size = [], [], 0
for r in rows:
    s = len(json.dumps(r, ensure_ascii=False).encode())
    if cur and size + s > 200000:  # database documents are capped at 256 KB
        parts.append(cur); cur, size = [], 0
    cur.append(r); size += s
if cur: parts.append(cur)
for i, p in enumerate(parts):
    json.dump({"rows": p}, open(f"{out}/hist-{i:02d}.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"parts": len(parts), "rows": len(rows), "updatedAt": h["updatedAt"], "from": h.get("from")}, open(f"{out}/meta.json", "w"), ensure_ascii=False)
import collections
print(len(rows), "rows in", len(parts), "parts")
c = collections.Counter(k for r in rows for k in r.get("x", {}))
print(dict(c))
