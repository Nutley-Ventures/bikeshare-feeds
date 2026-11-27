#!/usr/bin/env python3
"""Poll every GBFS feed in the MobilityData catalogue and save station and bike counts.

Runs on GitHub Actions once a week. Standard library only.
Starts from each system's auto-discovery file (gbfs.json), follows the feed
links it lists, and writes counts.json: per system, this week's stations,
docks and bikes, plus the bike count from each of the last 12 weeks.
Bikes being ridden or in the depot don't appear in a snapshot, so the bike
figure is the number visible at polling time, not the full fleet.
"""
import concurrent.futures as cf
import csv
import datetime as dt
import io
import json
import os
import sys
import urllib.request

CATALOGUE = os.environ.get("GBFS_CATALOGUE", "https://raw.githubusercontent.com/MobilityData/gbfs/master/systems.csv")
OUT = os.environ.get("COUNTS_FILE", "counts.json")
WORKERS, TIMEOUT, KEEP_DAYS = 32, 20, 84
UA = "bikeshare-feeds/1.0 (GBFS station and fleet counter)"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def feed_urls(disc):
    """Map feed name -> url from a gbfs.json of any version (1.x, 2.x, 3.x)."""
    data = disc.get("data") or {}
    feeds = data.get("feeds")
    if feeds is None:  # 1.x / 2.x: data.<language>.feeds
        langs = [v for v in data.values() if isinstance(v, dict) and "feeds" in v]
        pick = next((v for k, v in data.items() if k.lower().startswith("en") and isinstance(v, dict)), None)
        feeds = (pick or (langs[0] if langs else {})).get("feeds", [])
    return {f.get("name"): f.get("url") for f in feeds if f.get("name") and f.get("url")}


def stamp_of(doc):
    """A feed's own last_updated: seconds since 1970 in GBFS 1.x/2.x, a timestamp string in 3.x."""
    v = doc.get("last_updated")
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return dt.datetime.fromtimestamp(int(v), dt.timezone.utc)
        if isinstance(v, str):
            t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def poll(row):
    sid, url = row["System ID"].strip(), row["Auto-Discovery URL"].strip()
    out = {"id": sid}
    try:
        feeds = feed_urls(get_json(url))
        info_url, status_url = feeds.get("station_information"), feeds.get("station_status")
        free_url = feeds.get("vehicle_status") or feeds.get("free_bike_status")
        docked = 0
        tclass = {}  # vehicle_type_id -> eb (e-bike), pb (pedal bike), cb (cargo bike), sc (scooter), mo (moped), ot
        # Hardware and operator, where the feed states them (system_information, vehicle_types).
        if feeds.get("system_information"):
            try:
                si = get_json(feeds["system_information"]).get("data") or {}
                op = si.get("operator")
                if isinstance(op, list):  # 3.x: localised [{text, language}]
                    op = next((o.get("text") for o in op if str(o.get("language", "")).startswith("en")), None) \
                        or (op[0].get("text") if op else None)
                if isinstance(op, str) and op.strip():
                    out["op"] = op.strip()[:90]
            except Exception:
                pass
        if feeds.get("vehicle_types"):
            try:
                kinds = set()
                for v in (get_json(feeds["vehicle_types"]).get("data") or {}).get("vehicle_types") or []:
                    make, model = v.get("make"), v.get("model")
                    make = make[0].get("text") if isinstance(make, list) and make else make
                    model = model[0].get("text") if isinstance(model, list) and model else model
                    label = " ".join(str(x).strip() for x in (make, model) if x)
                    prop = v.get("propulsion_type") or ""
                    ff_ = v.get("form_factor") or ""
                    tclass[str(v.get("vehicle_type_id"))] = ("cb" if ff_ == "cargo_bicycle" else ("eb" if "electric" in prop else "pb") if ff_ in ("bicycle", "") else "sc" if ff_.startswith("scooter") else "mo" if ff_ == "moped" else "ot")
                    if v.get("form_factor") in (None, "bicycle", "cargo_bicycle") and (label or prop):
                        kinds.add(f"{label or 'Unspecified bike'}" + (f" ({prop.replace('_', ' ')})" if prop else ""))
                if kinds:
                    out["vt"] = sorted(kinds)[:6]
            except Exception:
                pass
        if info_url:
            stations = (get_json(info_url).get("data") or {}).get("stations") or []
            out["st"] = len(stations)
            out["dk"] = sum(int(s.get("capacity") or 0) for s in stations)
            pts = [(s.get("lat"), s.get("lon")) for s in stations if isinstance(s.get("lat"), (int, float)) and isinstance(s.get("lon"), (int, float))]
            if pts:  # centre of the network, for the map
                out["y"] = round(sum(p[0] for p in pts) / len(pts), 3)
                out["x"] = round(sum(p[1] for p in pts) / len(pts), 3)
        if status_url:
            status = get_json(status_url)
            if stamp_of(status):
                out["lu"] = stamp_of(status).strftime("%Y-%m-%dT%H:%MZ")  # when the operator last refreshed live data
            empty = full = reporting = 0
            for s in (status.get("data") or {}).get("stations") or []:
                avail = s.get("num_vehicles_available", s.get("num_bikes_available")) or 0
                off = s.get("num_vehicles_disabled", s.get("num_bikes_disabled")) or 0
                docked += int(avail) + int(off)
                if s.get("is_renting", True) in (False, 0):
                    continue  # closed stations don't count towards service levels
                reporting += 1
                if int(avail) == 0:
                    empty += 1  # nothing to rent here at this moment
                if "num_docks_available" in s and int(s.get("num_docks_available") or 0) == 0:
                    full += 1  # nowhere to return a bike here
            out["sr"], out["em"], out["fu"] = reporting, empty, full
        free = 0
        if free_url:
            fdoc = get_json(free_url)
            t = stamp_of(fdoc)
            if t and ("lu" not in out or t.strftime("%Y-%m-%dT%H:%MZ") > out["lu"]):
                out["lu"] = t.strftime("%Y-%m-%dT%H:%MZ")
            d = fdoc.get("data") or {}
            vehicles = d.get("vehicles", d.get("bikes")) or []
            free = sum(1 for v in vehicles if not v.get("station_id"))  # docked ones are already counted
            if tclass:  # free-floating fleet by kind of vehicle, so e-bikes and scooters can be told apart
                mix = {}
                for v in vehicles:
                    if v.get("station_id"):
                        continue
                    k = tclass.get(str(v.get("vehicle_type_id")), "ot")
                    mix[k] = mix.get(k, 0) + 1
                if mix:
                    out["mx"] = mix
        if not (info_url or status_url or free_url):
            raise ValueError("no station or vehicle feeds listed")
        out["bk"] = docked + free
        out["ff"] = free
    except Exception as e:  # one broken feed must not stop the rest
        out["err"] = f"{type(e).__name__}: {e}"[:160]
    return out


def main():
    req = urllib.request.Request(CATALOGUE, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))
    rows = [r for r in rows if (r.get("System ID") or "").strip() and not (r.get("Authentication Type") or "").strip()]

    try:
        prev = json.load(open(OUT, encoding="utf-8")).get("systems", {})
    except (OSError, ValueError):
        prev = {}

    now = dt.datetime.now(dt.timezone.utc)
    today, stamp = now.date().isoformat(), now.strftime("%Y-%m-%dT%H:%MZ")
    oldest = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()

    with cf.ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(poll, rows))

    systems, ok = {}, 0
    for res in results:
        sid = res.pop("id")
        old = prev.get(sid, {})
        hist = {d: n for d, n in (old.get("hist") or {}).items() if d >= oldest}
        if "err" in res:
            rec = {k: old[k] for k in ("st", "dk", "bk", "ff", "sr", "em", "fu", "at", "lu", "op", "vt", "mx", "y", "x") if k in old}  # keep last good numbers
            rec["err"] = res["err"]
        else:
            ok += 1
            rec = {**res, "at": stamp}
            hist[today] = res.get("bk", 0)
        if hist:
            rec["hist"] = dict(sorted(hist.items()))  # bikes seen each week, for trends
        systems[sid] = rec

    doc = {"updatedAt": stamp, "polled": len(rows), "answered": ok, "systems": systems}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print(f"{ok}/{len(rows)} feeds answered")
    if ok < len(rows) * 0.3:
        sys.exit("Fewer than 30% of feeds answered; check the runner's network.")


if __name__ == "__main__":
    main()
