# Authentication

All requests require a Bearer token in the `Authorization` header. Obtain your token from the LucidLink Admin Portal or your organization's identity provider.

## Header format

```
Authorization: Bearer <your-token>
```

## Python setup

```python
import httpx

API_BASE = "https://lucid-api.example.com/api/v1"
TOKEN = "your-bearer-token-here"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Reusable async client with connection pooling
client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0),
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
)
```

## Shell setup

```bash
# All subsequent curl examples assume these variables are set:
API_BASE="https://lucid-api.example.com/api/v1"
TOKEN="your-bearer-token-here"
```

