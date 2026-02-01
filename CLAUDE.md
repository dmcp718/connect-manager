# CONNECT Manager

Web app for importing S3 objects into LucidLink filespaces via External Data Store API.

## Stack
- **Backend**: FastAPI + Python 3.12, ARQ workers, Valkey (Redis-compatible)
- **Frontend**: HTMX + Alpine.js, custom CSS (LucidLink brand)
- **Services**: web (:8000), worker x2, valkey (:6379)

## Key Files
**Backend**: `app/main.py` (routes), `app/services/lucidlink.py` (API client), `app/services/s3_service.py` (boto3), `app/services/worker.py` (ARQ + SQS polling), `app/services/database.py` (SQLite), `app/services/sqs_service.py`, `app/services/sqs_poller.py`

**Frontend**: `app/templates/base.html` (layout), `app/templates/partials/*.html` (tabs/modals), `app/static/css/style.css`

## API Endpoints
```
POST /api/load-filespaces              # Load filespaces + DataStores
POST /api/create-datastore             # Create S3 DataStore
GET|DELETE /api/datastores/{id}/info   # DataStore info/delete
POST|DELETE /api/datastores/{id}/credentials  # S3 browsing creds
GET /api/browse/{datastore_id}         # Browse S3 bucket
POST /api/import/file|folder           # Import to LucidLink
GET /api/jobs, POST /api/jobs/{id}/cancel, DELETE /api/jobs/{id}
POST|DELETE /api/sqs/credentials       # SQS IAM creds
POST|DELETE /api/sqs/queues            # SQS queue config
POST /api/sqs/queues/{id}/pause|resume
```

## LucidLink REST API
```
GET  /filespaces
GET  /filespaces/{id}/external/data-stores
POST /filespaces/{id}/external/data-stores
DELETE /filespaces/{id}/external/data-stores/{dsId}
POST /filespaces/{id}/external/entries
```

## Development
```bash
./run.sh              # Start
./run.sh build        # Rebuild
./run.sh logs         # View logs
./run.sh help         # Show commands
```

## Brand Guidelines
- **Colors**: Charcoal (#151519), Neon (#B0FB15), Indigo (#5E53E0)
- **Typography**: Aeonik (headings), Inter (body)
- **Icons**: Lucide SVG, **Case**: Sentence case only

## SQS Event Stream
Auto-imports S3 objects via SQS notifications. Workers poll every 10s with distributed lock.

**Required IAM**: `sqs:CreateQueue`, `sqs:GetQueueAttributes`, `sqs:SetQueueAttributes`, `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `s3:GetBucketNotificationConfiguration`, `s3:PutBucketNotificationConfiguration`

**DB Tables**: `sqs_credentials`, `sqs_queues`, `sqs_events`
