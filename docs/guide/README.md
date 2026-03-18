# LucidLink Connect — Integration Guide

Import S3 objects into LucidLink filespaces using the External DataStore API.

---

## How it works

The LucidLink API lets you register an S3 bucket as an **External DataStore** and create **ExternalEntries** that reference individual S3 objects. These entries appear as normal files inside the filespace but are *lazy-loaded* — no data is copied at import time. When a user opens a file, LucidLink streams it directly from S3.

## Guide contents

| # | Page | Description |
|---|------|-------------|
| 1 | [Prerequisites](01-prerequisites.md) | Requirements, dependencies, and base URL |
| 2 | [Authentication](02-authentication.md) | Bearer token setup and reusable client |
| 3 | [Filespaces](03-filespaces.md) | List and select a filespace |
| 4 | [DataStores](04-data-stores.md) | List existing and create new S3 DataStores |
| 5 | [Browsing S3](05-browsing-s3.md) | Navigate bucket contents before importing |
| 6 | [Folder Structure](06-folder-structure.md) | Create the target directory tree in the filespace |
| 7 | [Importing Objects](07-importing-objects.md) | Import single files and batch folders |
| 8 | [Complete Workflow](08-complete-workflow.md) | End-to-end script tying all steps together |
| 9 | [Error Reference](09-error-reference.md) | HTTP status codes and connection errors |
| 10 | [Key Concepts](10-key-concepts.md) | Lazy loading, path mapping, idempotency, and more |

## Quick links

- **Just want the script?** Jump to [Complete Workflow](08-complete-workflow.md)
- **Debugging an error?** See [Error Reference](09-error-reference.md)
- **Understanding the model?** Read [Key Concepts](10-key-concepts.md)

## Base URL

All API calls use this pattern:

```
https://<lucid-api-host>/api/v1
```

Examples in this guide use `$API_BASE` (shell) or `API_BASE` (Python) as the base URL.
