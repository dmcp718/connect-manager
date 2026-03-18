# Prerequisites

## Requirements

| Requirement | Details |
|---|---|
| LucidLink API access | A valid API host URL and Bearer token |
| Filespace | An existing LucidLink filespace |
| S3 bucket | An accessible S3 bucket with objects to import |
| IAM credentials | `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` with `s3:ListBucket`, `s3:GetObject` permissions |
| Python 3.10+ | For the code examples (uses `httpx` and `boto3`) |

## Install dependencies

```bash
pip install httpx boto3
```

## Base URL

All API calls target:

```
https://<lucid-api-host>/api/v1
```

Examples use `$API_BASE` (shell) or `API_BASE` (Python) as a placeholder.

