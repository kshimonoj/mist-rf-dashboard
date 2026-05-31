# mist-rf-dashboard

A real-time monitoring dashboard for Juniper Mist wireless networks.
Visualize AP metrics, radio configurations, and configuration hierarchy across all sites.

## Features

- **Site Overview**: View all sites with AP online/offline status and online rate
- **AP List**: Per-site AP inventory with real-time channel, utilization, and client data
- **AP Detail**: Time-series graphs (1h/6h/24h/72h) for:
  - Connected clients
  - Channel utilization (total, TX, RX in BSS, Non-WiFi) per band (2.4G/5G/6G)
  - Noise floor, Tx Power, Channel, Bandwidth
- **Radio Config**: Current radio settings with configuration hierarchy detection
  - Org / Site (RF Template) / Device Profile / Device level detection per band
  - Configuration change detection and history
- **Floor Map**: Interactive floor plan view with AP overlay
  - Per-floor AP placement with channel-based color coding
  - Co-channel interference summary per band (2.4GHz / 5GHz / 6GHz)
  - AP details on hover (channel, bandwidth, power, noise floor, clients)
  - Disconnected APs shown with reduced opacity
- **Snapshot**: Save and replay 72-hour metric snapshots
  - Download/upload snapshot files (.db) for offline review
- **CSV Export**: Automatic hourly log export with manual save option
  ### CSV Logs
  - **AP Metrics**: channel, power, noise floor, utilization per radio band + model
  - **Floor Map Summary**: per-floor co-channel interference summary
    (band / channel / AP count / AP list / interference flag)
  - Auto-save on interval, manual save via "Save Now"
  - History page: CSV Logs tab shown first, sorted by timestamp descending
- **Settings (GUI)**: Configure API credentials, region, polling interval, and more via the browser UI
- **Dark/Light Mode**: Toggle via UI
- **Timezone Support**: Configurable timezone for all timestamps
- **Polling Control**: Adjustable polling interval via UI (no restart required)
- **Site Filtering**: Monitor specific sites only (useful for large-scale environments)

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
```

> **Note**: `.env` values are seeded into the database on first startup. After that, the GUI takes precedence. Use `docker compose down && docker compose up -d` (not `restart`) to reload `.env`.

> **Note**: `API_URL` is the backend URL as seen from the browser:
> - Local Mac: `http://localhost:8008`
> - Remote server: `http://<SERVER_IP>:8008`

## Settings (GUI)

Open the Settings panel by clicking the **Settings** button in the top-right corner of the dashboard.

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
| Polling Interval | How often to fetch data from the Mist API (30–3600 seconds) |
| Log Auto-Save Interval | How often to auto-save CSV logs (1–1440 minutes) |
| Log Retention Days | How long to keep CSV log files (1–365 days) |
| Timezone | Timezone for all timestamps (e.g. `Asia/Tokyo`, `UTC`) |
| Monitored Sites | Filter which sites to display and collect data for |

## 複数環境での設定

### API_URL と CORS_ORIGINS について

フロントエンドとバックエンドの通信設定は、`.env` で環境に合わせて変更します。

#### ローカル開発（Macbook）
```env
API_URL=http://localhost:8008
CORS_ORIGINS=http://localhost:3007
```

#### リモートサーバー（Ubuntu など）
```env
API_URL=http://192.168.19.150:8008
CORS_ORIGINS=http://localhost:3007,http://192.168.19.150:3007
```

#### 複数サイトでの運用
```env
API_URL=http://mist-dashboard.example.com:8008
CORS_ORIGINS=http://localhost:3007,http://mist-dashboard.example.com:3007,https://backup.example.com:3007
```

**注:** `CORS_ORIGINS` は複数のオリジンをカンマ区切りで指定できます。

## Switching Environments

To switch to a different Mist organization:

```bash
# 1. Use the GUI Settings page to update credentials (no restart needed)
# OR edit .env and restart:
docker compose down
docker compose up -d
```

> **Important**: `docker compose restart` does NOT reload `.env`. Always use `down` + `up`.

## Ports

| Service | Host Port | Container Port |
|---------|-----------|----------------|
| Frontend (Next.js) | 3007 | 3000 |
| Backend (FastAPI) | 8008 | 8000 |

## Data Persistence

All data is stored in the `./data/` directory:
```
data/
├── mist.db          # SQLite database (metrics, settings, credentials)
├── logs/            # Auto-saved CSV logs
└── snapshots/       # Snapshot database files (max 2 slots)
```

## Security Notes

- `.env` is excluded from git via `.gitignore` — **do not commit it**
- `data/` (SQLite database) is also excluded — **do not commit it**
- **Never** write your API token or Org ID directly in code or README files
- API tokens stored in the database are masked (`sk-t****`) in the GUI and API responses
- Use environment-specific `.env` files (e.g. `.env.prod`) and keep them local

## Tech Stack

- **Frontend**: Next.js 14, TypeScript, Tailwind CSS, Recharts, SWR
- **Backend**: FastAPI, SQLAlchemy, APScheduler, httpx
- **Database**: SQLite
- **Infrastructure**: Docker Compose

## License

MIT License. See [LICENSE](LICENSE) for details.
