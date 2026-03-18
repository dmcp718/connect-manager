# Error Reference

## HTTP status codes

| Status | Meaning | Action |
|---|---|---|
| `200` / `201` | Success | Process response data |
| `400` | Bad request | Check payload format. If body contains "already exists", treat as skip |
| `401` | Unauthorized | Token is invalid or expired — re-authenticate |
| `403` | Forbidden | Insufficient permissions for this filespace or operation |
| `404` | Not found | Resource (filespace, DataStore, entry path) does not exist |
| `409` | Conflict | Entry or folder already exists — safe to skip or resolve existing |
| `5xx` | Server error | Retry with exponential backoff |

## Connection errors

| Error | Cause |
|---|---|
| Timeout | API did not respond within 30 seconds — retry or verify API host |
| Connection refused | API host is unreachable — verify URL and network |
| SSL error | Certificate issue — verify API host URL scheme |

## Common scenarios

### Import returns 409 or 400 "already exists"

Both responses mean the ExternalEntry already exists at that path. This is safe to skip — imports are idempotent. See [Key Concepts — Idempotency](10-key-concepts.md#idempotency).

### Folder creation returns 409

Another process created the folder between your check and your create call. Resolve the path again to get the existing folder's ID and continue.

### 404 on DataStore operations

The filespace ID or DataStore ID is incorrect, or the resource was deleted. Re-list filespaces or DataStores to get current IDs.

