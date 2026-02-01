# CONNECT Manager - Development Context

## Project Overview

**LucidLink Labs: CONNECT Manager** - A web application for importing S3 objects into LucidLink filespaces using the External Data Store API.

## Architecture

### Tech Stack
- **Backend**: FastAPI + Python 3.12
- **Frontend**: HTMX + Alpine.js (minimal JavaScript)
- **Job Queue**: ARQ with Valkey (Redis-compatible)
- **Styling**: Custom CSS following LucidLink brand guidelines
- **Containerization**: Docker Compose

### Services (docker-compose.yml)
| Service | Port | Description |
|---------|------|-------------|
| web | 8000 | FastAPI application |
| worker (x2) | - | ARQ workers for import jobs |
| valkey | 6379 | Redis-compatible job queue backend |

## Key Files

### Backend
| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI routes and endpoints |
| `app/services/lucidlink.py` | LucidLink REST API client |
| `app/services/s3_service.py` | S3 operations (boto3) |
| `app/services/worker.py` | ARQ worker for import jobs |
| `app/services/state.py` | Application state management |
| `app/services/database.py` | SQLite persistence |
| `app/services/secrets.py` | Secure credential storage |

### Frontend
| File | Purpose |
|------|---------|
| `app/templates/base.html` | Main layout with tabs |
| `app/templates/partials/settings.html` | Settings tab |
| `app/templates/partials/browser.html` | S3 browser tab |
| `app/templates/partials/datastore_list.html` | DataStore list view |
| `app/templates/partials/job_queue.html` | Jobs tab |
| `app/static/css/style.css` | LucidLink brand CSS |
| `app/static/img/icons/` | SVG icons (Lucide) |

## API Endpoints

### LucidLink Configuration
- `POST /api/load-filespaces` - Load filespaces (auto-loads DataStores)
- `POST /api/load-datastores` - Load DataStores for filespace

### DataStore Management
- `GET /api/datastores/{id}/info` - Get DataStore configuration
- `DELETE /api/datastores/{id}` - Delete DataStore from LucidLink
- `POST /api/create-datastore` - Create new S3 DataStore
- `POST /api/datastores/{id}/credentials` - Save browsing credentials
- `DELETE /api/datastores/{id}/credentials` - Remove credentials

### S3 Browser
- `GET /api/browse/{datastore_id}` - Browse S3 bucket
- `POST /api/import/file` - Import single file
- `POST /api/import/folder` - Queue folder import job

### Jobs
- `GET /api/jobs` - List all jobs
- `POST /api/jobs/{id}/cancel` - Cancel running job
- `DELETE /api/jobs/{id}` - Delete job from history

## External API

LucidLink REST API (configured via API Endpoint):
```
GET  /filespaces                                    # List filespaces
GET  /filespaces/{id}/external/data-stores          # List DataStores
GET  /filespaces/{id}/external/data-stores/{dsId}   # Get DataStore info
POST /filespaces/{id}/external/data-stores          # Create DataStore
DELETE /filespaces/{id}/external/data-stores/{dsId} # Delete DataStore
POST /filespaces/{id}/external/entries              # Import file
```

## Development

### Run with wrapper scripts
```bash
# macOS/Linux
./run.sh              # Start
./run.sh build        # Rebuild and start
./run.sh logs         # View logs
./run.sh restart      # Restart

# Windows
run.bat               # Start
run.bat build         # Rebuild and start
run.bat logs          # View logs
```

### Run with Docker Compose directly
```bash
docker compose up --build -d
docker compose logs -f web
docker compose logs -f worker
```

### Manual development (requires Valkey)
```bash
cd app
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Brand Guidelines

Following LucidLink brand:
- **Colors**: Charcoal (#151519), Neon (#B0FB15), Indigo (#5E53E0)
- **Typography**: Aeonik (headings), Inter (body)
- **Icons**: Lucide SVG outline icons (MIT licensed)
- **Case**: Sentence case only (never title case or all caps)

## Recent Changes

- DataStore management UI (info modal, delete functionality)
- Auto-load DataStores when filespaces load
- Replace emojis with SVG icons
- Handle "already exists" as skipped (not error)
- Region field required for DataStore creation
