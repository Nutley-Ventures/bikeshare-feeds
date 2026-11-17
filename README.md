# bikeshare-feeds

Weekly data for the Bikeshare Systems Database.

- `poll_feeds.py` checks every public GBFS bikeshare feed and writes `counts.json` (stations, docks, bikes, free-floating, e-bike mix, operator).
- `ted_tenders.py` searches TED (EU tenders) for bikeshare and bicycle-supply notices and writes `tenders.json`.

GitHub Actions runs both on Mondays at 03:17 UTC and TED again on Wednesdays at 10:47 UTC. Use **Actions › Poll GBFS feeds and TED tenders › Run workflow** to run it by hand.
