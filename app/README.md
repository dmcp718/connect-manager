# LucidLink Labs - S3 to LucidLink Integrator

A web-based tool for importing S3 objects into LucidLink filespaces.

## Features

- **S3 Browser**: Navigate and explore S3 buckets
- **LucidLink Integration**: Connect to filespaces via REST API
- **File Import**: Import individual files or entire folders recursively
- **DataStore Management**: Create and manage S3 datastores in LucidLink
- **Real-time Progress**: SSE-based progress tracking and activity logs
- **Multi-threaded Import**: Parallel bulk import with 10 concurrent workers

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: HTMX + Alpine.js (minimal JavaScript)
- **Styling**: Custom CSS with LucidLink brand guidelines
- **Async HTTP**: httpx for LucidLink API, boto3 for S3

## Quick Start

```bash
cd app
./run.sh
```

Then open http://localhost:8000 in your browser.

## Manual Setup

```bash
cd app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

## Configuration

### S3 Access
- Uses AWS default credentials (environment variables, ~/.aws/credentials, or IAM role)
- Optional: Provide explicit access key and secret in the UI

### LucidLink API
- Requires LucidLink daemon running locally on port 3003
- Obtain API token from LucidLink client

## Project Structure

```
app/
├── main.py                 # FastAPI application
├── services/
│   ├── lucidlink.py        # LucidLink API client
│   ├── s3_service.py       # S3 operations
│   └── state.py            # Application state
├── templates/
│   ├── base.html           # Layout template
│   ├── index.html          # Entry point
│   └── partials/           # HTMX partial templates
├── static/
│   ├── css/style.css       # LucidLink brand CSS
│   ├── fonts/              # Aeonik font files
│   └── img/                # Lab flask icon
├── requirements.txt
└── run.sh
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Main page |
| POST | `/api/load-filespaces` | Load available filespaces |
| POST | `/api/load-datastores` | Load datastores for filespace |
| POST | `/api/connect` | Connect to S3 and LucidLink |
| GET | `/api/browse` | Browse S3 bucket contents |
| POST | `/api/import/file` | Import single file |
| POST | `/api/import/folder` | Import folder recursively |
| GET | `/api/logs/stream` | SSE log stream |
| GET | `/api/progress/stream` | SSE progress stream |

## Brand Guidelines

This application follows LucidLink brand guidelines:

- **Primary Colors**: Charcoal (#151519), Neon (#B0FB15), White
- **Typography**: Aeonik (headings), Inter (body)
- **Sentence case only** - Never title case or all caps
