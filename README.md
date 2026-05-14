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
- **Snapshot**: Save and replay 72-hour metric snapshots
  - Download/upload snapshot files (.db) for offline review
- **CSV Export**: Automatic hourly log export with manual save option
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

2. Create `.env` file from the example:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` with your Mist credentials:
   ```
   MIST_API_TOKEN=your_api_token
   MIST_ORG_ID=your_org_id
   MIST_BASE_URL=https://api.mist.com/api/v1
   POLLING_INTERVAL_SECONDS=300
   TIMEZONE=Asia/Tokyo
   ```

   > **Note**: Adjust `MIST_BASE_URL` for your region:
   > - Global: `https://api.mist.com/api/v1`
   > - EU: `https://api.eu.mist.com/api/v1`
   > - APAC (AC2): `https://api.ac2.mist.com/api/v1`
   > - APAC (AC5): `https://api.ac5.mist.com/api/v1`

4. Start the application:
   ```bash
   docker-compose up --build
   ```

5. Open your browser at `http://localhost:3007`

## Switching Environments

To switch to a different Mist organization:

```bash
# 1. Edit .env with new credentials
# 2. (Optional) Remove old data for a clean start
rm -rf data/
# 3. Restart containers (required to reload .env)
docker-compose down
docker-compose up -d
```

> **Important**: `docker-compose restart` does NOT reload `.env`. Always use `down` + `up`.

## Ports

| Service | Host Port | Container Port |
|---------|-----------|----------------|
| Frontend (Next.js) | 3007 | 3000 |
| Backend (FastAPI) | 8008 | 8000 |

## Data Persistence

All data is stored in the `./data/` directory:
```
data/
├── mist.db          # SQLite database (metrics, settings)
├── logs/            # Auto-saved CSV logs
└── snapshots/       # Snapshot database files (max 2 slots)
```

## Tech Stack

- **Frontend**: Next.js 14, TypeScript, Tailwind CSS, Recharts, SWR
- **Backend**: FastAPI, SQLAlchemy, APScheduler, httpx
- **Database**: SQLite
- **Infrastructure**: Docker Compose

## License

MIT License. See [LICENSE](LICENSE) for details.
