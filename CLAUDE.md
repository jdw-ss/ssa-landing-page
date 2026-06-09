# SSA Landing Page

## Snapshot

Static HTML/CSS/JS landing page for **sportsbookscienceanalytics.com** (apex + www). Served by an nginx-style Cloud Run service. Status: **live**.

## Stack

- **Language**: HTML / CSS / JS (static)
- **Framework**: none — pure static
- **Hosting**: Cloud Run service serving static files

## Run locally

```bash
python -m http.server 8080 --directory "/Users/johnwilson/Claude Projects/ssa-landing-page"
```

`.claude/launch.json` config: `ssa-landing`.

## Cloud infrastructure

- **GCP project**: `golf-data-projects` (shared with `golf-dashboard`)
- **Region**: `us-east1`
- **Cloud Run service**: `ssa-landing`
- **Custom domains**: `sportsbookscienceanalytics.com` (apex) + `www.sportsbookscienceanalytics.com`

## Schedules

None.

## External connections

None.

## Deploy

- **Command**: `./deploy.sh`
- **Preview required?** Yes (UI changes)
- **Predeploy guard**: deploy.sh enforces explicit `--region=us-east1`.
- **Health check**: `curl` BOTH apex and www must return 200. Deploy script checks both.

## Companion docs

- `README.md` (if present)

## Related projects

- **`golf-tournament-predictor`** — **same GCP project (`golf-data-projects`)** AND shares the `sportsbookscienceanalytics.com` custom domain; region drift in either project's deploy.sh affects routing here

See `~/Claude Projects/docs/PROJECT_INDEX.md` for the full cross-project map and shared-infrastructure clusters.

## Gotchas

- **Homepage Live / Coming-Soon badges are manual and drift.** Each league card's `badge-live` / `badge-soon` class (and the `card live` modifier) in `index.html` is hand-set here — it is NOT derived from whether the league's public host is actually serving. When a league launches its public surface, its card must be flipped by hand to `class="card live"` + `badge-live` "Live". CFL sat stale at "Coming Soon" for a while after its public ATS page went live for exactly this reason. When NBA / NHL / NCAAF launch publicly, update their cards here in the same effort.
- Two domain mappings (apex + www). When debugging cache / cert issues, check **both** with `gcloud beta run domain-mappings list --region=us-east1 --project=golf-data-projects` — recreating either triggers ~15min–several-hours of managed-cert provisioning (see `ANALYTICS_PROJECT_GUIDELINES.md` → Key Lessons).
- Lives in the same GCP project as `golf-dashboard`. Region drift in golf-dashboard's deploy.sh can affect AR / source buckets shared with this project.
