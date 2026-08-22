# mist-rf-dashboard

A real-time monitoring and troubleshooting dashboard for Juniper Mist wireless networks.
Visualize AP metrics, radio configurations, client experience (SLE), and automatically detect
Wi-Fi issues across all sites.

![Site Overview](docs/screenshots/01-home.png)

## Features

### Monitoring
- **Site Overview**: All sites with AP online/offline status and online rate at a glance
- **Site Detail**: SLE summary + sortable AP List and Client List, tabbed per site
- **AP Detail**: Time-series graphs (1h/6h/24h/72h, plus an optional 30-day view) for connected
  clients, channel utilization
  (total, TX, RX in BSS, Non-WiFi per band), noise floor, Tx power, channel, and bandwidth
- **Client List / Client Detail**: Per-client RSSI, SNR, TX/RX rate, throughput, band/channel
  history with roaming markers
- **Floor Map**: Interactive floor plan with AP overlay, channel-based color coding, and
  co-channel interference summary per band
- **Floor Peak**: pick the busiest moment of a site over a period and chart the top-20 APs by
  connected clients on each floor (see [Floor Peak Analysis](#floor-peak-analysis-busiest-moment-per-floor))
- **RRM**: count RRM / radar channel changes across sites, split them into RADAR / POST_RADAR / RRM,
  and show the metrics just before and just after each change
  (see [RRM / RADAR Channel-Change Analysis](#rrm--radar-channel-change-analysis))

![Site Detail with SLE](docs/screenshots/02-site-detail.png)

### Client Experience (SLE)
- Capacity / Throughput / Coverage / Time to Connect / Roaming / AP Availability, at both the
  site level and per-AP level
- Capacity classifier breakdown (Wi-Fi interference, non-Wi-Fi interference, client count,
  client usage) to pinpoint *why* a score is low

### Radio Configuration
- Configuration hierarchy detection per band: Org / Site (RF Template) / Device Profile / Device
- Automatic change detection and history (channel, bandwidth, Tx power, enable/disable)

![AP Detail](docs/screenshots/03-ap-detail.png)

### Insights — Automated Issue Detection
- **Sticky Client**: clients staying on a weak-signal AP instead of roaming
- **2.4GHz stuck**: dual-band clients stuck on 2.4GHz (band steering failure)
- **High Retry**: abnormal TX retry rate per client
- **Co-channel interference**: AP pairs on the same channel with high mutual interference
- **Roaming flapping**: clients bouncing between APs too frequently
- **Config Change Impact**: automatic before/after comparison (SLE, utilization, retry rate)
  around every radio configuration change, with an Improved / Degraded / Neutral verdict
- **Recommendations**: rule-based optimization suggestions per AP (e.g. free channel
  suggestions for co-channel conflicts)
- Active / History view — issues are tracked from first detection to resolution, not just a
  point-in-time snapshot

![Insights](docs/screenshots/04-insights.png)

### Operations
- **Tags**: attach free-form tags to APs and clients, and filter a dedicated view by tag
- **Snapshot**: freeze and replay a full 72-hour metrics window at any point in time — save up
  to 2 slots, then download/upload `.db` files to review the exact same graphs offline or on a
  different machine, even after the live data has rolled off
- **CSV Export**: automatic hourly export (AP metrics, SLE metrics, client metrics, floor map
  summary) with manual "Save Now", searchable/filterable History page
- **Pseudonymized download**: download CSV logs and Hang AP analysis results with AP names,
  site names, MACs, IPs and timestamps consistently replaced — converted on the fly, so no
  second copy is ever stored, and pseudonymized files can be **restored** afterwards
  (see [Pseudonymized download](#pseudonymized-download))
- **Multi-Environment**: register multiple Mist orgs (e.g. per customer/site) and switch between
  them from the GUI — no container restart required
- **Settings (GUI)**: API credentials, region, polling interval, log retention, timezone, and
  monitored-site filtering, all configurable via the browser
- **Dark/Light Mode** and full timezone support for all timestamps

![Snapshots](docs/screenshots/05-snapshots.png)

![CSV Logs History](docs/screenshots/05b-history-csv.png)

## Requirements

- Docker & Docker Compose
- Juniper Mist account with API token

## Setup

1. Clone the repository:
   ```bash
   git clone git@github.com:kshimonoj/mist-rf-dashboard.git
   cd mist-rf-dashboard
   ```

2. Start the application:
   ```bash
   docker compose up -d
   ```

3. Open `http://localhost:3007` in your browser.

4. Configure your credentials in the Settings page (see [Settings (GUI)](#settings-gui) below).

### Optional: Configure via .env

If you prefer to set credentials via environment variables instead of the GUI:

```bash
cp .env.example .env
```

Edit `.env` with your Mist credentials:
```
MIST_API_TOKEN=your_api_token
MIST_ORG_ID=your_org_id
MIST_BASE_URL=https://api.mist.com/api/v1
POLLING_INTERVAL_SECONDS=300
TIMEZONE=Asia/Tokyo
API_URL=http://localhost:8008
CORS_ORIGINS=http://localhost:3007
```

> **Note**: `.env` values are seeded into the database on first startup. After that, the GUI
> takes precedence. Use `docker compose down && docker compose up -d` (not `restart`) to reload
> `.env`.

> **Note**: `API_URL` is the backend URL as seen from the browser:
> - Local machine: `http://localhost:8008`
> - Remote server: `http://<SERVER_IP>:8008`

> **Note**: `CORS_ORIGINS` is a comma-separated list of allowed origins. Add your server's IP if
> accessing from a remote browser (e.g. `http://localhost:3007,http://192.168.1.100:3007`).

## Settings (GUI)

Open the Settings panel from the **Settings** button in the header.

![Settings](docs/screenshots/06-settings.png)

### Environments (multi-org support)

Register one or more Mist environments (org + token + region) and switch the active one at any
time. Switching clears the locally cached metrics/insights for a clean start in the new
environment; tags are preserved across environments.

### API Credentials

| Field | Description |
|-------|-------------|
| API Token | Your Mist API token. Leave blank to keep the existing token. |
| Org ID | Your Mist organization ID (UUID format). |
| Region | Select the Mist region that matches your organization's location. |

Changes take effect on the **next polling cycle** without requiring a restart.

#### Finding Your Region

The region can be identified from the URL in the Mist portal:

| Portal URL | Region |
|------------|--------|
| `manage.mist.com` | Global 01 |
| `manage.gc1.mist.com` | Global 02 (GC1) |
| `manage.ac2.mist.com` | Global 03 / APAC (AC2) |
| `manage.eu.mist.com` | EMEA 01 (EU) |

For the full list of regions, refer to the official documentation:
[Juniper Mist API Endpoint URLs and Global Regions](https://www.juniper.net/documentation/jp/ja/software/mist/automation-integration/topics/topic-map/api-endpoint-url-global-regions.html)

### Other Settings

| Setting | Description |
|---------|-------------|
| Polling Interval | How often to fetch AP data from the Mist API (30–3600 seconds) |
| Client Polling Interval | How often to fetch the client list (minimum 5 minutes) |
| Log Auto-Save Interval | How often to auto-save CSV logs (1–1440 minutes) |
| Log Retention Days | How long to keep CSV log files (1–365 days) |
| **30-Day Metrics History** | Off by default. When enabled, adds a `30d` option to the SLE and AP Detail graph selectors, and extends the local metrics retention (see below) |
| Timezone | Timezone for all timestamps (e.g. `Asia/Tokyo`, `UTC`) |
| Monitored Sites | Filter which sites to display and collect data for |

### Metrics Retention & 30-Day History

The raw AP/client metrics stored in `mist.db` are **not** covered by "Log Retention Days" (that
setting only applies to exported CSV files). By default, raw metrics are pruned nightly
(03:00) after **7 days** to keep the database small.

Enabling **30-Day Metrics History** in Settings does two things:

1. Raises local metrics retention to 30 days (nightly prune keeps the last 30 days instead of 7)
2. Unlocks a `30d` button next to `1h / 6h / 24h / 72h` on:
   - the SLE cards (Site Detail and AP Detail) — queried live from the Mist API, no local
     storage impact
   - AP Detail's METRICS HISTORY graphs — queried from the local database. To keep 30-day
     graphs fast, this view is automatically returned as **hourly averages** instead of raw
     samples (channel/bandwidth show the latest value per hour instead of an average)

> **Note**: Turning this on increases database size, especially with many APs and a short
> polling interval. A confirmation dialog is shown before enabling it. Turning it back off
> shrinks retention to 7 days again on the next nightly prune — export any CSV logs you want to
> keep before switching back.

## Switching Environments (Mist org)

You can switch to a different Mist organization directly from the Settings GUI (Environments
section) — no restart needed. Alternatively, edit `.env` and reload:

```bash
docker compose down
docker compose up -d
```

> **Important**: `docker compose restart` does NOT reload `.env`. Always use `down` + `up`.

## Ports

| Service | Host Port | Container Port |
|---------|-----------|----------------|
| Frontend (Next.js) | 3007 | 3000 |
| Backend (FastAPI) | 8008 | 8000 |

## Hang AP Detection (offline log analysis)

`backend/hangap/` analyses exported Mist logs offline (no network access, no LLM calls):

- `hangap.loader.load()` merges the split CSV / XLSX log files, normalises them, and reports
  duplicates and **gaps** (missing sampling periods).
- `hangap.detector.detect()` finds zero-client intervals — an AP that stays `connected` while
  its client count sits at zero — and correlates AP events (±30 min by default) around the end
  of each interval.
- `hangap.neighbors.build_context()` adds **nearby-AP columns**, so a zero interval can be told
  apart from "nobody was there" (see below).

```python
from hangap import detect, load

res = load("/path/to/logs/2026-08-09")
df = detect(res.metrics, res.events, res.gaps,
            rf_neighbors=res.rf_neighbors,
            window_start="2026-08-09 16:00", window_end="2026-08-09 21:00")
```

Always pass `gaps`. Without it, a zero interval is silently joined across a missing period and
the zero-sample count becomes too large — it never raises an error, so the mistake is hard to
notice. Gaps with zero actually-missing samples (sampling jitter, e.g. a 460 s gap on a 300 s
interval) never truncate an interval — only a gap that dropped at least one sample does.

An interval starts on exactly one condition: **the previous sample had `num_clients >= 1`** and
this one is zero. That rule does not change around a gap — an interval truncated by a gap does
not resume on the far side, because the first sample after the gap has a zero predecessor. A
hang spanning a gap is still reported, as the `打ち切り(欠測)` interval before it. Without this
rule an AP that is merely always at zero gets emitted once per gap: on the demo environment that
inflated 479 real intervals to 2345.

`window_start` / `window_end` are both optional. When either is given, the analysis is
restricted to the samples inside `[window_start, window_end)`: the interval end, the recovery
check, the following client count, `AP最大clients` and the site-wide totals never look outside
it. A run of zeros still going at `window_end` is reported as `継続中`, so the zero-sample count
is bounded by the window (a 6-hour window at a 5-minute interval tops out at 72 samples).
Without that bound an interval runs as far past the requested period as the loaded data allows —
a 6-hour window over a week of stadium logs produced a zero interval that ended six days later.

Event correlation is the one exception: events are matched against `zero-end ± event_window`, so
events after `window_end` are still picked up. Events are a separate log from the metrics, and
that lookup is bounded by `event_window`, so it cannot run away.

One consequence: the sample at the head of the window has no predecessor inside the window, so
an interval that drops to zero right after `window_start` is not detected. That is by design, so
it is not a warning — it is stated in the analysis-conditions line of every run instead. Start
the window a little earlier if you need those intervals.

If the loaded data doesn't actually cover the requested window (a common risk when joining
hourly History Log files), `detect()` emits a `UserWarning` — it never raises an error:

- data starts after `window_start` (by at least one sampling interval) → part of the requested
  period has no samples at all
- data doesn't reach `window_end` → a trailing interval may be misclassified as "ongoing" when
  it would actually have recovered given more log
- event collection lags the metrics — `metrics-end − events-end` is greater than
  `log_save_interval` → event collection may have stopped, so event correlation can be missing

The event check deliberately does **not** ask whether events reach the window. "The time of the
last event" says nothing about how fresh the collection is: events are sparse (55 in 11 days at
a real site), so a healthy collection routinely has its last event hours ago, and that test fired
while collection was perfectly up to date. Comparing against the metrics end works because the
metrics always have data for every AP, so their end really does mark how far collection got. With
no metrics at all there is nothing to compare against and the warning is skipped.

### `min_zero_samples` counts samples, not time

**The sampling interval differs per environment** (measured: 30 s in the demo environment,
5 min at customer sites). The default `min_zero_samples=5` therefore means 25 minutes at a
5-minute interval but only **2.5 minutes** at a 30-second interval. When the interval is
unknown or mixed, use `min_zero_duration` (a time span) instead — it takes precedence over
`min_zero_samples`. `hangap.loader` reports the estimated interval per AP, so check it first.

Ongoing intervals (`継続中`) and site-wide exodus candidates (`退場疑い=True`) are kept in the
result on purpose; filtering is left to the caller.

### Nearby APs are decided by distance, not by RF adjacency

Without this, a zero-client interval cannot be told apart from "there simply was nobody there".
A nearby AP of X is: **same `map_id`**, the closest `neighbor_count` APs by Euclidean distance
over `x_m` / `y_m`, and within `max_distance_m`. APs on another map are never neighbours (the
coordinate systems differ, so no distance exists). If no AP falls inside the limit — or the AP
has no coordinates at all (e.g. logs in the older 33-column `ap_metrics_v1` format) — the
verdict is `判定不能` (undecidable), which is **not** the same as zero neighbours.

This is measured, not assumed. On a real 250-AP / 7-map site: 49.8 % of RF neighbours were on a
different map, RSSI top-N and distance top-N agreed only 46.2 % of the time (N=4), RSSI top-N
averaged 18.9 m away versus 11.9 m for distance top-N, and only 28.7 % of RF adjacencies were
observed in both directions. The question being answered is "were there people *at that spot*",
and physical proximity answers it better. `周辺AP RF隣接数` is a **reference column only** — how
many of the distance-picked neighbours also appear in `rf_neighbors`. It never affects the
verdict, and it is left blank (never an error) when `rf_neighbors` was not loaded.

Eight columns are appended to the result; the existing 22 columns keep their names and order.
`周辺AP端末数` is each neighbour's **mean `num_clients` during the interval** (zero-start to
zero-end), and `周辺AP判定` is `周辺に端末あり` when `周辺AP端末数合計 >=
neighbor_client_threshold`. Like `継続中` and `退場疑い`, this verdict never filters rows — it is
material for the reader to judge with.

**Every neighbour client count is measured, never estimated.** APs do not all poll on the same
phase, so on a short interval a neighbour's sample can fall just outside the window. The lookup
window is therefore widened by **half that AP's estimated sampling interval** on each side —
half, because anything wider would reach into the adjacent polling cycle and mix a genuinely
out-of-interval value into the mean. If a neighbour still has no sample in the widened window,
it is reported as `実測なし` rather than back-filled with an earlier value, it is left out of
`周辺AP端末数合計` (adding it as 0 would read as "nobody was around"), and `周辺AP実測なし数`
says how many neighbours that happened to. `--explain` prints `実測なし` in place of the number,
so the reasoning on screen is always traceable to a real measurement.

> **The defaults are provisional.** `neighbor_count=4`, `max_distance_m=25`, and
> `neighbor_client_threshold=1.0` are starting points to be tuned against real site data, not
> settled values. Use `--explain <AP_NAME>` (repeatable) to print the reasoning per interval and
> check whether the thresholds hold up on your data.

### Data-quality warning on truncated intervals

If more than `--truncated-warn-ratio` (default 0.3) of the detected intervals ended as
`打ち切り(欠測)`, `analyze` prints a warning that includes the ratio, and records it in the
output files too (the xlsx warning row and the CSV `_summary.txt`) so that whoever receives only
the results cannot misread them. A high ratio means log collection was intermittent and the
result does not support analysis — a count alone would not reveal that.

```bash
python -m hangap analyze /path/to/logs --out ./out \
    --neighbor-count 4 --max-distance-m 25 --neighbor-client-threshold 1.0 \
    --truncated-warn-ratio 0.3 --explain AP04
```

### Choosing which sites to analyse

`--site <site_id|site_name>` (repeatable) limits the analysis to those sites; omitting it covers
every site in the logs. The selection is applied in the loader, **before** interval estimation
and detection, so the report and the result describe the same data. Derived columns do not move
when a site is selected: the site-wide trend (`退場疑い`) is aggregated per `site_name` and the
nearby-AP verdict per `map_id`, both of which are already site-scoped. `ap_events` is not
filtered — events are matched per AP by name, so other sites' events cannot reach the result.
A site that is not in the logs is an input error naming the sites that were not found. The
selected sites are recorded in the analysis conditions, so a saved result still says what it
covered. On the Hang AP page the run button stays disabled until a site (or "all sites") is
chosen — the point of the selection is to stop other sites' intervals from padding the table, so
nothing is analysed implicitly. When the logs hold a single site it is selected automatically.

`GET /api/hangap/sites` lists the sites the logs actually contain — `site_id`, `site_name`, AP
count and the period covered — so the UI can offer them as choices. It is built **from the logs,
not from `/api/sites`**: after switching environments `data/logs` still holds sites that are no
longer monitored, and building the choices from the monitored list would make those logs
unanalysable. It reads only the four columns it needs from each `ap_metrics` file and caches the
result in-process (invalidated when a file is added or changed; `?refresh=true` forces a re-read).

```bash
python -m hangap analyze /path/to/logs --out ./out --site "Head Office" --site "Branch A"
curl -s localhost:8008/api/hangap/sites
curl -s -X POST localhost:8008/api/hangap/analyze -H 'Content-Type: application/json' \
     -d '{"sites": ["<site_id>"]}'
```

### "No data at all" is not "nothing detected"

If the loader reads **zero `ap_metrics` rows**, the run is an error — `analyze` exits with code 1
and the API job ends as `failed`. Detecting no hang while metrics *were* read stays a normal
result (exit 0 / `done`, 0 rows). The two look identical in a count alone, and reading a missing
log directory as "no hangs" is the easier mistake to make.

### On-demand analysis over the API

The same analysis runs on this dashboard's own `data/logs` via `/api/hangap`. It is started by
request only — nothing is analysed on a schedule. Reading 5,000+ files takes far longer than a
request, so it is an asynchronous job with progress polling, and **only one job runs at a time**
(a second request gets 409 with the running `job_id`). Results are kept in-process, never in
`mist.db`: at most 3 jobs, discarded an hour after they finish. A job's working files go to a
`tempfile` directory, never into `data/logs` — writing them there would feed the next analysis
its own output.

```bash
curl -s -X POST localhost:8008/api/hangap/analyze -H 'Content-Type: application/json' -d '{}'
curl -s localhost:8008/api/hangap/jobs/<job_id>            # status / phase / summary / warnings
curl -s 'localhost:8008/api/hangap/jobs/<job_id>/result?offset=0&limit=100&status=回復&sort=ゼロ開始&order=desc'
curl -s 'localhost:8008/api/hangap/jobs/<job_id>/download?format=xlsx' -o result.xlsx
curl -s -X DELETE localhost:8008/api/hangap/jobs/<job_id>
```

Result rows can be filtered per column with repeated `filter=<column>:<operator>:<value>`
parameters. The operator depends on the column (`hangap.table` classifies every one of the 30
columns and the response returns that classification as `column_kinds` / `enum_choices`, so the
UI never redefines it): `contains` for text, `in` for the columns with a fixed set of values
(repeat it to select several — same column is OR), `min` / `max` for numbers, `from` / `to` for
timestamps, `is` for booleans. Different columns are AND-ed, and filtering happens server-side so
it composes with paging and sorting. **Downloads ignore filters entirely** — they return the file
the analysis wrote, always all rows and all 30 columns.

```bash
curl -s -G 'localhost:8008/api/hangap/jobs/<job_id>/result' \
  --data-urlencode 'filter=ap_name:contains:AP-01' --data-urlencode 'filter=回復状況:in:継続中' \
  --data-urlencode 'filter=連続ゼロ回数:min:5' --data-urlencode 'filter=退場疑い:is:true'
```

The request body accepts the same conditions as the CLI (`from`, `to`, `sites`, `min_zero_samples`,
`min_zero_duration`, `event_window_minutes`, `exodus_threshold`, `gap_factor`, `neighbor_count`,
`max_distance_m`, `neighbor_client_threshold`, `truncated_warn_ratio`); every field is optional
and the defaults are the CLI's own. Both paths call `hangap.analysis`, so the downloaded files
are byte-for-byte what the CLI writes — including the xlsx condition, warning and recovered-row
fills.

### Saved analysis results

A job that ends `done` is archived automatically to `data/hangap_results/` as one **set** of
three files sharing a timestamp — `hangap_result_<YYYYMMDD_HHMMSS>.{xlsx,csv,json}`. The xlsx
and csv are copies of what the job wrote (same bytes as the download); the json holds what the
filename cannot — interval count, recovery / nearby-AP breakdowns, the analysis conditions,
warning count and data period. Jobs that end `failed` (timeout, zero `ap_metrics`) archive
nothing. The Hang AP page lists the sets newest-first; clicking a row shows that result in the
same table component the fresh results use — paging, sorting, filtering and column order all
work, and the page states the save time so a past result is not mistaken for a new one.

`results/<name>/rows` reads the saved csv back — it **never re-runs the analysis** — and returns
the same shape as `jobs/<job_id>/result`, so the UI needs no branch of its own.

```bash
curl -s localhost:8008/api/hangap/results
curl -s 'localhost:8008/api/hangap/results/hangap_result_20260816_101500/rows?offset=0&limit=100'
curl -s 'localhost:8008/api/hangap/results/hangap_result_20260816_101500/download?format=xlsx' -o result.xlsx
curl -s -X DELETE localhost:8008/api/hangap/results/hangap_result_20260816_101500
```

Rotation runs right after each save and is deliberately self-contained: it only ever reads and
deletes inside `data/hangap_results/`, its total-size figure counts only the files it is able to
delete, and the newest set is never removed. Sets go oldest-first, **whole set at a time**, until
both limits are met.

| Variable | Default | Meaning |
|---|---|---|
| `HANGAP_RESULTS_MAX_FILES` | `50` | Number of result **sets** to keep (xlsx+csv+json = 1 set) |
| `HANGAP_RESULTS_MAX_TOTAL_MB` | `500` | Total size cap for `data/hangap_results/` |

`data/hangap_results/` is excluded from log scanning (`hangap.loader.EXCLUDED_DIR_NAMES`), so an
archived result is never picked up as input by the next analysis.

## Floor Peak Analysis (busiest moment, per floor)

`backend/floorpeak/` answers a different question from Hang AP: **for one site over a period,
which moment was the busiest, and which APs on a floor carried the clients at that moment.**
It reads the same collected CSV logs offline (no network access, no LLM calls) and shares no
code with `hangap` beyond reusing `hangap.loader` to read `ap_metrics` — the same logs must not
be parsed two different ways.

### How the peak is chosen

Samples are **bucketed before they are summed**. AP timestamps are nearly aligned in practice but
carry a few seconds of jitter; summing by raw timestamp makes "the sample where the most APs
happened to land on the same second" win, which misses the real peak. The bucket width is the
sampling interval `hangap.loader` estimates (300 s in this deployment); if it cannot be
estimated, floorpeak falls back to 300 s **and says so in the warnings**. Within a bucket the
latest row per AP is used, ties on the total pick the **earliest** bucket, and the window is the
half-open interval `[from, to)`. APs that are down contribute 0 and are not special-cased.

`--at` selects the bucket nearest a given moment instead. If the chosen bucket is further than
`3 × bucket_seconds` away, that is a warning, not an error. **`--at` ignores `--from` / `--to`**
(narrowing the window first would pin "the nearest bucket" to the window edge and make the choice
impossible to explain); the analysis records that they were ignored as a warning.

### How floor names are resolved

`ap_metrics` carries `map_id` but no readable floor name, and its CSV schema is not changed (the
Hang AP analysis and the pseudonymizer both detect file types by exact header match). Floor names
come from the hourly `floormap_*_summary.csv`, whose rows are (map_name × band × channel) and
whose `ap_list` only holds the APs on that band/channel.

1. The **single** `floormap_*_summary.csv` closest to the peak is read (hourly collection means at
   most ~30 minutes of skew, and floor layout does not change by the minute). Candidates are
   picked by the filename timestamp and verified by the file's own `timestamp` column.
2. Rows are filtered by `site_name` (there is no `site_id` in that file), then every `ap_list` is
   split to build `ap_name → map_name`. **Bands are not filtered** — an AP that only appears on
   one band would otherwise be dropped.
3. That mapping is joined against the peak-moment `ap_metrics` to derive `map_id → map_name`, and
   **each AP's floor is finally decided by its `map_id`.** This is the point of the design: an AP
   whose radios are all down appears in no `ap_list` at all, yet still lands on the right floor.
4. One `map_id` pointing at several names takes the majority and warns. An empty or unknown
   `map_id` becomes `（未割当）` — kept as a floor of its own, never dropped, and counted in a
   warning. A floormap more than 24 h away from the peak is not used at all: every AP becomes
   `（未割当）` and the warning says why.

### CLI

```bash
python -m floorpeak analyze --logs /path/to/logs --site <site_id> \
  --from '2026-08-18 08:00' --to '2026-08-18 20:00' --out ./out
python -m floorpeak analyze --logs /path/to/logs --site 'Head Office' \
  --at '2026-08-18 14:00' --floor 'Head Office 5F' --out ./out
```

The site is **required and single** — "the peak for the whole site" is undefined across several
sites. Output is `floorpeak_result_<stamp>.{xlsx,csv}` plus a `_summary.txt`; the csv holds every
AP of the site (~250 rows), because picking a floor and cutting the top 20 is the display's job,
not the analysis's. Column names are ASCII (`ap_name, mac, model, num_clients, status, map_id,
map_name, x_m, y_m, rank_in_floor`) so the result can later be added to the pseudonymizer without
tripping its non-ASCII leak check. `rank_in_floor` is the per-floor descending order of
`num_clients`, ties broken by `ap_name` so the order never shuffles between runs.

The xlsx has two sheets. **`chart`** draws the selected floor's top 20 as a horizontal `BarChart`;
the bars are one series coloured per data point by AP model, and because Excel's own legend cannot
show model names for a single series, the legend is drawn as filled cells next to the chart.
**`data`** holds every floor's every row plus the conditions and warnings. Files are not split per
floor — the sheet states which floor the chart is for.

### API

```bash
curl -s localhost:8008/api/floorpeak/sites
curl -s -X POST localhost:8008/api/floorpeak/analyze -H 'Content-Type: application/json' \
  -d '{"site":"<site_id>","from":"2026-08-18 08:00","to":"2026-08-18 20:00"}'
curl -s localhost:8008/api/floorpeak/jobs/<job_id>            # status / phase / meta / warnings
curl -s localhost:8008/api/floorpeak/jobs/<job_id>/result     # rows + meta + warnings
curl -s 'localhost:8008/api/floorpeak/jobs/<job_id>/download?format=xlsx&floor=<map_name>' -o peak.xlsx
curl -s localhost:8008/api/floorpeak/results
curl -s localhost:8008/api/floorpeak/results/floorpeak_result_20260818_140000/rows
curl -s -X DELETE localhost:8008/api/floorpeak/results/floorpeak_result_20260818_140000
```

The body takes `site` (required), `from`, `to`, `at`; unknown fields are a 400 rather than a
silent no-op. One job runs at a time (409 otherwise), results live in the process only, and both
CLI and API go through `floorpeak.analysis`, so the downloaded csv is byte-for-byte what the CLI
writes. `?floor=` is the only thing built on the fly — an xlsx chart can only show one floor, so
the download re-renders that sheet for the floor you are looking at; without it you get the stored
file. `results/<name>/rows` reads the saved csv and json back and **never re-runs the analysis**,
returning the same shape as `jobs/<job_id>/result`.

`meta` carries everything needed to read the chart without guessing: site, requested window,
`selected_by` (`auto` / `manual`), the peak time and the real sample range inside that bucket, the
site-wide client total, `bucket_seconds`, the floormap file used and how far it is from the peak,
the floor list, and the model→colour table the UI paints with (defined once, in the backend).

Results are archived to `data/floorpeak_results/` as `floorpeak_result_<YYYYMMDD_HHMMSS>.{xlsx,csv,json}`
with the same whole-set rotation as Hang AP, driven by its **own** limits so neither analysis can
rotate the other's records away. `floorpeak_results` is in `hangap.loader.EXCLUDED_DIR_NAMES`, so
saved results are never read back as input.

| Variable | Default | Meaning |
|---|---|---|
| `FLOORPEAK_RESULTS_MAX_FILES` | `50` | Number of result **sets** to keep (xlsx+csv+json = 1 set) |
| `FLOORPEAK_RESULTS_MAX_TOTAL_MB` | `500` | Total size cap for `data/floorpeak_results/` |

## RRM / RADAR Channel-Change Analysis

`backend/rrm/` is the first analysis that treats **`ap_events` as its primary data source**
(Hang AP only ever used it as a side channel). For a set of sites over a period it answers:
**how often did RRM change a channel, why, and how many clients were on the AP when it happened.**
It reads the same collected CSV logs offline (no network access, no LLM calls) and shares no code
with `hangap` / `floorpeak` beyond reusing `hangap.loader` to read the logs.

### What counts as a channel change

Only `AP_RRM_ACTION` rows where `pre_channel != channel`. They are split into three classes:

| Class | Rule |
|---|---|
| `RADAR` | `reason = radar-detected` |
| `POST_RADAR` | `reason = post-radar` |
| `RRM` | every other reason (`scheduled-site-rrm`, `interference-*`, `auto-channel-selection`, …) |

`post-radar` gets its own class because putting it in either of the other two misrepresents it —
it is the follow-up work after a radar hit, not the hit itself and not ordinary RRM.

Rows where `pre_channel == channel` are **not** channel changes. They are periodic RRM evaluating
the situation and deciding to keep the current channel — normal behaviour, not a fault. They are
counted separately as **no-op** per class and stay in the detail table; hiding them would hide the
fact that RRM is running at all.

### Radar detections are counted independently

`AP_RADAR_DETECTED` carries its own `pre_channel → channel`, and in real logs some detections have
**no matching `AP_RRM_ACTION` at all**. Counting only `AP_RRM_ACTION` therefore loses radar events.
The summary reports detections, how many of them changed a channel, and how many had no matching
action (same AP, `AP_RRM_ACTION` with `reason = radar-detected`, within ±300 s). One detection with
three nearby actions is still **one** detection — the match is decided per detection, never per action.

`AP_CONFIG_CHANGED_BY_RRM` has no `reason` and appears in roughly the same numbers as
`AP_RRM_ACTION`, so it is **not used** in the analysis. Its count is still reported, labelled as a
reference figure, so a future decision has data to stand on.

### Before / after metrics

For each event the **single sample immediately before and the single sample immediately after** are
taken from `ap_metrics` for the same AP (matched on `ap_mac`) — no averaging, since an average
smooths away exactly what the change did. "Before" is the last sample strictly earlier than the
event; "after" is the first sample at or later than it. The sampling interval comes from
`hangap.loader`'s estimate (30 s in this deployment); if it cannot be estimated the analysis falls
back to 300 s **and says so in the warnings**.

`match_status` records why a row could not be matched: `no_before`, `no_after`, `too_far` (a sample
is 3× the interval away or more) or `no_ap` (the AP has no samples at all). Those rows keep their
raw values and timestamps so the gap is visible, but their **deltas are left empty** and the row is
counted as *unmatched* in the summary. Nothing is dropped.

A row is flagged `contaminated` when another channel-change event for the same AP
(`AP_RRM_ACTION` or `AP_RADAR_DETECTED`, any band) falls inside the before→after interval. Utilization
is reported for all three bands, so a change on another band moves the numbers too. Contaminated
rows are **kept and marked**, never removed — the point is to show which rows you cannot trust.

`impact_clients` is `clients_before`: the number of clients attached to the AP right before the
channel changed. Aggregated impact sums only count rows that actually changed channel.

### Output

The csv/xlsx columns are fixed (and ASCII, so a future pseudonymizer schema can be added without
tripping its non-ASCII leak check):

```
event_timestamp, classification, reason, site_name, ap_name, ap_mac, band,
pre_channel, post_channel, channel_changed,
before_timestamp, after_timestamp, match_status, contaminated,
clients_before, clients_after, clients_delta,
util_24_before, util_24_after, util_24_delta,
util_5_before,  util_5_after,  util_5_delta,
util_6_before,  util_6_after,  util_6_delta,
impact_clients
```

There is deliberately **no channel delta** column: the "+16" in 36→52 has no physical meaning, so
`pre_channel` and `post_channel` are shown side by side instead.

The xlsx has three sheets — `chart` (stacked bar of channel changes per hour, plus impact per
class), `data` (every detail row) and `summary` (per class, per site, per AP and the full hourly
series). When there are more hourly buckets than the chart can show legibly, the chart is limited
to the most recent ones and **the sheet says so**; the full series stays in `summary` and the csv.

### CLI

```bash
python -m rrm analyze --logs /path/to/logs --out ./out            # every site
python -m rrm analyze --logs /path/to/logs --out ./out   --site '1_Kyobashi' --site '3_HPEN_Osaka'   --from '2026-08-20 00:00' --to '2026-08-22 00:00'
```

`--site` may be repeated and accepts a `site_id` or a `site_name`; omitting it analyses every site.
Unlike Floor Peak there is no single-site requirement, because nothing here is defined per-site the
way "the site's peak" is. Exit codes: `0` OK, `1` input error (unknown site, unreadable logs, no
`ap_events` at all), `2` output error.

### API

```bash
curl -s localhost:8008/api/rrm/sites
curl -s -X POST localhost:8008/api/rrm/analyze -H 'Content-Type: application/json' \
  -d '{"sites":["<site_id>"],"from":"2026-08-20 00:00","to":"2026-08-22 00:00"}'
curl -s localhost:8008/api/rrm/jobs/<job_id>            # status / phase / meta / warnings
curl -s localhost:8008/api/rrm/jobs/<job_id>/result     # rows + meta + warnings
curl -s 'localhost:8008/api/rrm/jobs/<job_id>/download?format=xlsx' -o rrm.xlsx
curl -s localhost:8008/api/rrm/results
curl -s localhost:8008/api/rrm/results/rrm_result_20260822_231245/rows
curl -s -X DELETE localhost:8008/api/rrm/results/rrm_result_20260822_231245
```

CLI and API go through `rrm.analysis`, so the downloaded csv is byte-for-byte what the CLI writes.
`results/<name>/rows` reads the saved csv and json back and **never re-runs the analysis**,
returning the same shape as `jobs/<job_id>/result`. One job runs at a time (a second POST gets 409
with the running `job_id`).

An empty window is a **result, not a failure**: if RRM did not act during the period the job
completes with zero rows and a warning saying so. "No `ap_events` in the logs at all" is a failure,
because that means there was nothing to analyse.

Results are archived to `data/rrm_results/` as `rrm_result_<YYYYMMDD_HHMMSS>.{xlsx,csv,json}` with
the same whole-set rotation as Hang AP and Floor Peak, driven by its **own** limits so no analysis
can rotate another's records away. `rrm_results` is in `hangap.loader.EXCLUDED_DIR_NAMES`, so saved
results are never read back as input.

| Variable | Default | Meaning |
|---|---|---|
| `RRM_RESULTS_MAX_FILES` | `50` | Number of result **sets** to keep (xlsx+csv+json = 1 set) |
| `RRM_RESULTS_MAX_TOTAL_MB` | `500` | Total size cap for `data/rrm_results/` |

## Data Persistence

All data is stored in the `./data/` directory:
```
data/
├── mist.db           # SQLite database (metrics, settings, credentials, tags, insights)
├── logs/             # Auto-saved CSV logs (AP / SLE / client metrics, floor map summary)
├── hangap_results/   # Saved hang-AP analysis results (xlsx + csv + json per run, rotated)
├── floorpeak_results/ # Saved floor-peak analysis results (xlsx + csv + json per run, rotated)
├── rrm_results/      # Saved RRM / RADAR analysis results (xlsx + csv + json per run, rotated)
└── snapshots/        # Snapshot database files (max 2 slots)
```

### CSV log rotation (`data/logs/`)

Rotation runs after each auto-save job and works purely on the filesystem: the files it counts are
exactly the files it can delete — every file **directly under** `data/logs/` (subdirectories such as
`data/hangap_results/` are never scanned or touched). Files go oldest-first by mtime, by age first
and then by size cap, and the newest `10` files of every kind (`ap_metrics`, `sle_metrics`,
`client_metrics`, `floormap`, `ap_events`, `rf_neighbors`) are never removed. Deleting an
`ap_metrics` file also removes its `Snapshot` row, and files with no `Snapshot` row are rotated too.

**Deletion is disabled by default.** The job logs what it *would* delete and stops there; review
those `[ROTATE][DRY-RUN]` lines before enabling it.

| Variable | Default | Meaning |
|---|---|---|
| `LOG_ROTATE_DRY_RUN` | `1` | `1` = log the deletion plan only. Set to `0` to actually delete |
| `LOG_MAX_TOTAL_MB` | `5000` | Total size cap for files directly under `data/logs/` |

The retention period comes from **log retention** in the Settings GUI (default 30 days).

## Pseudonymized download

CSV logs (History page) and saved Hang AP analysis results can be downloaded in a
**pseudonymized** form, for sharing with vendors or for use in write-ups.

- History: select one or more files, then **仮名化ダウンロード** (multiple files come back as a ZIP,
  up to 50 per request).
- Hang AP → saved results: **仮名化 csv** on each row. `xlsx` is *not* supported — its first three
  rows are free-form title / conditions / warnings text containing site names, `site_id` and
  timestamps, which a column whitelist cannot cover.

Conversion happens **at download time**; no pseudonymized copy is ever written to `data/logs`.
AP names, site names, MAC addresses, IPs, SSIDs, hostnames and floor-map names are replaced with
stable pseudonyms (`AP_0001`, `SITE_001`, …) and all timestamps are shifted by a fixed offset.
Output is verified before it is returned (leak check): if anything unconverted is detected the
request fails with `422` and **no file is returned**.

**This is pseudonymization, not anonymization.** The number of APs, client counts and event
patterns are preserved, so re-identification risk is not zero.

### The salt and the mapping are the whole point

Consistency ("the same AP always gets the same pseudonym") is guaranteed by two files:

```
data/.pseudonym_salt.json    # salt + time offset, mode 0600
data/.pseudonym_map.json     # original value -> sequence number, mode 0600
```

They live in `data/` — deliberately **outside `data/logs`**, so they can never be listed or
fetched through the log APIs. `data/` is git-ignored, and both filenames are ignored by name too.

**If you lose these files there is no recovery.** New files will be created and the same AP will
get a different pseudonym, so anything you previously shared can no longer be correlated with
anything you share afterwards. Back them up somewhere safe outside the repository.

### Restoring (re-identification)

Files you pseudonymized, processed locally and merged can be turned back into real values:
**仮名化を復元** on the History tab (upload the processed files) or

```bash
cd backend
python -m pseudonymizer restore merged.csv --out ~/restored
```

Because the input is an already-processed file, the column whitelist cannot be used; restoring is
plain text replacement driven by `data/.pseudonym_map.json` (longest pseudonym first, on word
boundaries) plus the inverse time shift. Values that are not in the mapping — aggregates and labels
created during processing — pass through unchanged. `vlan_id` cannot be restored: its pseudonym is
a bare integer that is indistinguishable from real data (pseudonymize with `--keep-vlan` if you
need it). Every run prints a report with the replacement counts and warns — with counts and
locations, never values — if pseudonym-looking strings are left over, which is how you notice a
stale mapping or a file pseudonymized with a different salt.

Uploads are processed in a temporary directory and deleted afterwards; nothing is written under
`data/`. **The restored file contains real names, MACs, IPs and real timestamps** — handle it the
same way you would handle the original logs.

Details, the per-column rules and the CLI equivalent: [`backend/pseudonymizer/README.md`](backend/pseudonymizer/README.md).

## Security Notes

- `.env` is excluded from git via `.gitignore` — **do not commit it**
- `data/` (SQLite database, CSV logs, pseudonymizer salt/mapping) is also excluded — **do not commit it**
- **Never** write your API token or Org ID directly in code or README files
- API tokens stored in the database are masked (`abcd****`) in the GUI and API responses
- `mist_base_url` is restricted to `*.mist.com` to prevent SSRF / token exfiltration
- Use environment-specific `.env` files (e.g. `.env.prod`) and keep them local

### Protecting the Credentials API

The `POST /api/credentials` endpoint modifies stored Mist API tokens. If your backend port
(8008) is reachable by untrusted clients, set `SETTINGS_SECRET` in `.env`:

```env
SETTINGS_SECRET=your_random_secret_here  # openssl rand -hex 32
```

When set, the endpoint requires an `X-Settings-Key: <secret>` header. The GUI will prompt for
the Admin Key automatically. If `SETTINGS_SECRET` is empty, the endpoint is open — acceptable
for localhost-only deployments.

## Troubleshooting

### Settings > ENVIRONMENTS shows "Loading..." forever

If you are accessing the dashboard from a different machine (e.g. `http://192.168.x.x:3007`),
add your server's IP to `CORS_ORIGINS` in `.env`:

```
CORS_ORIGINS=http://localhost:3007,http://192.168.x.x:3007
```

Then restart the backend container:
```bash
docker compose restart backend
```

### After `git pull`, the backend fails with `no such column: ...`

A new release added columns to an existing table. Run the migration manually:

```bash
docker compose exec backend python3 -c "from database import migrate_db; migrate_db(); print('done')"
```

If the container was not running yet, start it first, then run the command above.

## Mist API Reference

This dashboard is built entirely on the Juniper Mist REST API. For reference, here are the
endpoints in use:

### Org / Site
| Endpoint | Purpose |
|---|---|
| `GET /orgs/{org_id}/sites` | List sites |
| `GET /orgs/{org_id}/rftemplates` | RF Templates (for radio config hierarchy detection) |
| `GET /orgs/{org_id}/deviceprofiles` | Device Profiles (for radio config hierarchy detection) |
| `GET /sites/{site_id}/setting` | Site settings (RF Template assignment) |

### Access Points
| Endpoint | Purpose |
|---|---|
| `GET /sites/{site_id}/devices?type=ap&limit=1000` | Bulk AP configuration (paginated via `X-Page-Total`) |
| `GET /sites/{site_id}/devices/{ap_id}` | Single AP configuration detail |
| `GET /sites/{site_id}/stats/devices?type=ap` | AP real-time stats (channel, utilization, noise floor, etc.) |
| `GET /sites/{site_id}/devices/events/search?duration=` | AP events (reboot, DFS, etc.) |

### Clients
| Endpoint | Purpose |
|---|---|
| `GET /sites/{site_id}/stats/clients` | Currently connected clients with real-time RSSI/SNR/rates. **Note**: `limit` and `ap_mac` query parameters are documented but do not filter/paginate in practice — the endpoint always returns the full site-wide list. |
| `GET /sites/{site_id}/clients/search` | Historical client connection search. `limit` and `ap_mac` filtering work correctly here, but RSSI/SNR/rate fields are not included. |

### SLE (Service Level Experience)
| Endpoint | Purpose |
|---|---|
| `GET /sites/{site_id}/sle/site/{site_id}/metric/{metric}/summary?duration=` | Site-level SLE (capacity, throughput, coverage, time-to-connect, roaming, ap-availability) |
| `GET /sites/{site_id}/sle/ap/{ap_id}/metric/{metric}/summary?duration=` | AP-level SLE (same 6 metrics) |
| `GET /sites/{site_id}/sle/site/{site_id}/metric/{metric}/classifier/{classifier}/summary?duration=` | Classifier breakdown (e.g. wifi-interference, non-wifi-interference for the capacity metric) |

> All endpoints are called with `Authorization: Token <api_token>`. See the [official Mist API
> reference](https://www.juniper.net/documentation/us/en/software/mist/api/http/api/introduction)
> for full parameter documentation.

## Tech Stack

- **Frontend**: Next.js 14, TypeScript, Tailwind CSS, Recharts, SWR
- **Backend**: FastAPI, SQLAlchemy, APScheduler, httpx
- **Database**: SQLite
- **Infrastructure**: Docker Compose

## License

MIT License. See [LICENSE](LICENSE) for details.
