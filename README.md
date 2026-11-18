# bikeshare-feeds

Free, scheduled collection of public data on bike-sharing systems and bikeshare tenders, used by the Bikeshare Systems Database. Python standard library only; every request uses an honest user agent.

## Workflows

| Workflow | When (UTC) | What it runs | Output |
| --- | --- | --- | --- |
| `poll.yml` | Mondays 03:17 (feeds and TED), Wednesdays 10:47 (TED and portals), daily 06:41 (slow portals only) | `poll_feeds.py`, `split_for_db.py`, `ted_tenders.py`, `world_tenders.py` | `counts.json`, `db/counts-N.json`, `tenders.json`, `world_tenders.json` |
| `history.yml` | Sundays 22:37 | `ted_history.py`, `tender_db.py` | `desk/tender_history.json`, `db/tenders/` |
| `desk.yml` | 2nd of each month 04:23 | `desk_fetch.py` | `desk/` (Wikipedia list of bicycle-sharing systems, Wikidata) |

Each workflow also has a **Run workflow** button under Actions.

## Scripts

- `poll_feeds.py` checks every public GBFS bikeshare feed (MobilityData's systems list) and writes stations, docks, bikes, free-floating vehicles, vehicle types and operator per system to `counts.json`.
- `split_for_db.py` splits `counts.json` into database-sized `db/counts-N.json` files.
- `ted_tenders.py` searches TED, the EU's official tender journal, through its free Search API for bikeshare, micromobility and bicycle-supply notices from the last 120 days (`tenders.json`).
- `world_tenders.py` searches free national portals: UK Find a Tender and Contracts Finder, Quebec SEAO, Brazil PNCP, Colombia SECOP II, France BOAMP, Germany's public procurement data service and Spain's public sector procurement platform (`world_tenders.json`). Slow portals read a slice per run and carry on next time.
- `ted_history.py` searches TED back to 2016 for bikeshare tenders and awards, including descriptions and winners where TED has them (`desk/tender_history.json`).
- `tender_db.py` reads hardware facts from the notice text (fleet size, e-bikes, docked or free floating, stations, contract years, supply or operation), drops search artefacts and splits the history into `db/tenders/hist-NN.json` (under 256 KB each) with `meta.json`.
- `desk_fetch.py` fetches Wikipedia's list of bicycle-sharing systems and Wikidata entries for the desktop study.

## Sources and licences

GBFS feeds and the MobilityData systems list (open data, per feed licence); TED (EU open data, free to reuse); the national portals' open data services; Wikipedia (CC BY-SA) and Wikidata (CC0).
