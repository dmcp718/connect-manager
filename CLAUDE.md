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
| `app/services/worker.py` | ARQ worker for import jobs + SQS polling cron |
| `app/services/state.py` | Application state management |
| `app/services/database.py` | SQLite persistence |
| `app/services/secrets.py` | Secure credential storage |
| `app/services/sqs_service.py` | SQS queue management + S3 notification config |
| `app/services/sqs_poller.py` | ARQ cron job for polling SQS queues |

### Frontend
| File | Purpose |
|------|---------|
| `app/templates/base.html` | Main layout with tabs |
| `app/templates/partials/settings.html` | Settings tab |
| `app/templates/partials/browser.html` | S3 browser tab |
| `app/templates/partials/datastore_list.html` | DataStore list view |
| `app/templates/partials/job_queue.html` | Jobs tab |
| `app/templates/partials/sqs_tab.html` | AWS SQS tab |
| `app/templates/partials/sqs_queue_list.html` | SQS queue list component |
| `app/templates/partials/sqs_queue_modal.html` | Add/create queue modal |
| `app/templates/partials/sqs_queue_info.html` | Queue details modal |
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

### AWS SQS Event Stream
- `GET /api/tab/sqs` - SQS tab content
- `POST /api/sqs/credentials` - Save SQS IAM credentials
- `DELETE /api/sqs/credentials` - Remove SQS credentials
- `GET /api/sqs/queues/add-modal` - Show add queue modal
- `POST /api/sqs/queues` - Create or add SQS queue
- `DELETE /api/sqs/queues/{id}` - Delete queue configuration
- `POST /api/sqs/queues/{id}/pause` - Pause queue polling
- `POST /api/sqs/queues/{id}/resume` - Resume queue polling
- `GET /api/sqs/queues/{id}/info` - Queue details modal
- `GET /api/sqs/events` - List recent SQS events

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

## AWS SQS Event Stream Feature

Automatic S3-to-LucidLink imports triggered by S3 event notifications via SQS queues.

### How It Works
1. User provides IAM credentials with SQS + S3 permissions
2. User creates a new SQS queue (or uses existing)
3. App auto-configures:
   - SQS queue policy (allows S3 to send messages)
   - S3 bucket event notifications (ObjectCreated events → SQS)
4. Workers poll SQS every 10 seconds (distributed lock prevents duplicates)
5. S3 events trigger automatic imports to LucidLink

### Required IAM Permissions
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:CreateQueue",
        "sqs:GetQueueAttributes",
        "sqs:SetQueueAttributes",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage"
      ],
      "Resource": "arn:aws:sqs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketNotificationConfiguration",
        "s3:PutBucketNotificationConfiguration"
      ],
      "Resource": "arn:aws:s3:::*"
    }
  ]
}
```

### Database Tables
- `sqs_credentials` - IAM credentials for SQS access
- `sqs_queues` - Configured queue configurations
- `sqs_events` - Tracked S3 events and their import status

## Recent Changes

- **AWS SQS event stream** - Automatic imports from S3 event notifications
- DataStore management UI (info modal, delete functionality)
- Auto-load DataStores when filespaces load
- Replace emojis with SVG icons
- Handle "already exists" as skipped (not error)
- Region field required for DataStore creation
