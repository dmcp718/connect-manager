# LucidLink Labs: CONNECT Manager

A web application for importing S3 objects into LucidLink filespaces using the External Data Store API.

## Features

- **DataStore Management** - Create, view, and delete S3 DataStores in LucidLink
- **S3 Browser** - Navigate and explore S3 buckets linked to DataStores
- **Bulk Import** - Import individual files or entire folders with parallel processing
- **Job Queue** - Track import progress with real-time updates
- **Activity Logs** - Real-time SSE-based logging

## Quick Start

### Docker Compose (Recommended)

```bash
docker compose up --build
```

Open http://localhost:8000 in your browser.

### Manual Setup

```bash
cd app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Note: Manual setup requires a separate Valkey/Redis instance for the job queue.

## Configuration

### LucidLink API

1. Obtain an API token from your LucidLink admin console
2. Enter the API endpoint (e.g., `https://api.lucidlink.com/api/v1`)
3. Paste your Bearer token
4. Click "Load Filespaces"

### Creating a DataStore

1. Select a filespace from the dropdown
2. Click "+ Create new" in the DataStores card
3. Fill in the required fields:
   - **Name** - Unique identifier for the DataStore
   - **Bucket** - S3 bucket name
   - **Region** - AWS region (required)
   - **Access Key / Secret Key** - AWS credentials with bucket access
4. Click "Create DataStore"

### Browsing and Importing

1. Go to the **Browser** tab
2. If prompted, enter AWS credentials for the DataStore
3. Navigate folders and click:
   - **Import** - Import a single file
   - **+ Queue** - Queue a folder for bulk import
4. Monitor progress in the **Jobs** tab

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose                            │
├─────────────┬─────────────┬─────────────┬───────────────────┤
│   Web App   │  Worker 1   │  Worker 2   │      Valkey       │
│  (FastAPI)  │   (ARQ)     │   (ARQ)     │  (Job Queue)      │
│   :8000     │             │             │     :6379         │
└──────┬──────┴──────┬──────┴──────┬──────┴─────────┬─────────┘
       │             │             │                │
       ▼             ▼             ▼                │
┌─────────────────────────────────────────────┐    │
│           LucidLink REST API                │    │
│    (External Data Store Management)         │    │
└─────────────────────────────────────────────┘    │
       │                                           │
       ▼                                           │
┌─────────────────────────────────────────────┐    │
│              S3 Buckets                     │◄───┘
│         (via boto3/httpx)                   │
└─────────────────────────────────────────────┘
```

## Project Structure

```
lucidlink-connect-web-ui/
├── app/
│   ├── main.py                 # FastAPI application
│   ├── services/
│   │   ├── lucidlink.py        # LucidLink API client
│   │   ├── s3_service.py       # S3 operations
│   │   ├── worker.py           # ARQ job worker
│   │   ├── state.py            # Application state
│   │   ├── database.py         # SQLite persistence
│   │   └── secrets.py          # Credential storage
│   ├── templates/
│   │   ├── base.html           # Layout template
│   │   └── partials/           # HTMX partial templates
│   └── static/
│       ├── css/style.css       # LucidLink brand CSS
│       ├── fonts/              # Aeonik font files
│       └── img/icons/          # SVG icons
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## API Documentation

Swagger UI is available at your API endpoint + `/docs`:
- Example: `https://api.lucidlink.com/api/v1/docs`

## Tech Stack

- **Backend**: FastAPI (Python 3.12)
- **Frontend**: HTMX + Alpine.js
- **Job Queue**: ARQ + Valkey
- **Styling**: Custom CSS (LucidLink brand guidelines)
- **Icons**: Lucide (MIT licensed)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LL_API_HOST` | `https://dev-admin-api...` | LucidLink API endpoint |
| `VALKEY_HOST` | `localhost` | Valkey/Redis host |
| `VALKEY_PORT` | `6379` | Valkey/Redis port |
| `DATA_DIR` | `/data` | Persistent data directory |
| `ARQ_MAX_JOBS` | `4` | Max concurrent jobs per worker |

## License

Proprietary - LucidLink Corporation
