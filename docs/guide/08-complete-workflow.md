# Complete Workflow

A self-contained script that performs the entire workflow end-to-end. This combines all previous steps into a single runnable file.

For details on individual steps, see:
- [Authentication](02-authentication.md) — Bearer token setup
- [Filespaces](03-filespaces.md) — Listing and selecting a filespace
- [DataStores](04-data-stores.md) — Creating an S3 DataStore
- [Folder Structure](06-folder-structure.md) — Building the directory tree
- [Importing Objects](07-importing-objects.md) — Creating ExternalEntries

## Script

```python
#!/usr/bin/env python3
"""
LucidLink External DataStore — Complete Import Workflow

Imports all objects from an S3 prefix into a LucidLink filespace.
"""

import asyncio
import boto3
import httpx

# ─── Configuration ────────────────────────────────────────────
API_BASE      = "https://lucid-api.example.com/api/v1"
TOKEN         = "your-bearer-token"
AWS_ACCESS    = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET    = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_REGION    = "us-east-1"
BUCKET_NAME   = "my-media-bucket"
IMPORT_PREFIX = "videos/2025/"   # S3 prefix to import (empty for entire bucket)
# ──────────────────────────────────────────────────────────────

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:

        # ── Step 1: List filespaces ──
        print("Fetching filespaces...")
        resp = await client.get(f"{API_BASE}/filespaces", headers=HEADERS)
        resp.raise_for_status()
        filespaces = resp.json().get("data", resp.json())

        print("Available filespaces:")
        for i, fs in enumerate(filespaces):
            print(f"  [{i}] {fs['name']}  ({fs['id']})")

        # Select filespace (hardcoded index for automation, or prompt user)
        filespace = filespaces[0]
        fs_id = filespace["id"]
        print(f"\nUsing filespace: {filespace['name']}")

        # ── Step 2: List or create DataStore ──
        print("\nFetching DataStores...")
        resp = await client.get(
            f"{API_BASE}/filespaces/{fs_id}/external/data-stores",
            headers=HEADERS,
        )
        resp.raise_for_status()
        datastores = resp.json().get("data", resp.json())

        # Find existing DataStore for our bucket, or create one
        ds_id = None
        for ds in datastores:
            if ds.get("s3StorageParams", {}).get("bucketName") == BUCKET_NAME:
                ds_id = ds["id"]
                print(f"Found existing DataStore: {ds['name']} ({ds_id})")
                break

        if not ds_id:
            print(f"Creating DataStore for bucket '{BUCKET_NAME}'...")
            resp = await client.post(
                f"{API_BASE}/filespaces/{fs_id}/external/data-stores",
                headers=HEADERS,
                json={
                    "name": f"S3 — {BUCKET_NAME}",
                    "kind": "S3DataStore",
                    "s3StorageParams": {
                        "bucketName": BUCKET_NAME,
                        "accessKey": AWS_ACCESS,
                        "secretKey": AWS_SECRET,
                        "region": AWS_REGION,
                        "useVirtualAddressing": True,
                        "urlExpirationMinutes": 10080,
                    },
                },
            )
            resp.raise_for_status()
            ds_id = resp.json().get("data", resp.json()).get("id")
            print(f"Created DataStore: {ds_id}")

        # ── Step 3: List S3 objects ──
        print(f"\nScanning S3 bucket '{BUCKET_NAME}' prefix '{IMPORT_PREFIX}'...")
        s3 = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS,
            aws_secret_access_key=AWS_SECRET,
            region_name=AWS_REGION,
        )
        paginator = s3.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=IMPORT_PREFIX):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith("/"):
                    keys.append(obj["Key"])

        print(f"Found {len(keys)} objects to import")
        if not keys:
            print("Nothing to import.")
            return

        # ── Step 4: Create folder structure ──
        print("\nCreating folder structure...")
        unique_dirs = sorted(set(
            k.rsplit("/", 1)[0] for k in keys if "/" in k
        ))

        for dir_path in unique_dirs:
            parts = dir_path.split("/")
            current_path = ""
            parent_id = None

            # Resolve root
            r = await client.get(
                f"{API_BASE}/filespaces/{fs_id}/entries/resolve",
                headers=HEADERS,
                params={"path": "/"},
            )
            if r.status_code == 200:
                parent_id = r.json().get("data", r.json()).get("id")

            for folder_name in parts:
                current_path += f"/{folder_name}"
                r = await client.get(
                    f"{API_BASE}/filespaces/{fs_id}/entries/resolve",
                    headers=HEADERS,
                    params={"path": f"/{BUCKET_NAME}/{current_path.lstrip('/')}"},
                )
                if r.status_code == 200:
                    parent_id = r.json().get("data", r.json()).get("id")
                    continue

                # Create folder
                r = await client.post(
                    f"{API_BASE}/filespaces/{fs_id}/entries",
                    headers=HEADERS,
                    json={"name": folder_name, "type": "dir", "parentId": parent_id},
                )
                if r.status_code in (200, 201):
                    parent_id = r.json().get("data", r.json()).get("id")
                elif r.status_code == 409:
                    # Already exists (race condition) — resolve it
                    r2 = await client.get(
                        f"{API_BASE}/filespaces/{fs_id}/entries/resolve",
                        headers=HEADERS,
                        params={"path": f"/{BUCKET_NAME}/{current_path.lstrip('/')}"},
                    )
                    if r2.status_code == 200:
                        parent_id = r2.json().get("data", r2.json()).get("id")

        # ── Step 5: Import files ──
        print(f"\nImporting {len(keys)} files...")
        created = skipped = failed = 0
        batch_size = 25

        for i in range(0, len(keys), batch_size):
            batch = keys[i : i + batch_size]

            async def do_import(s3_key):
                ll_path = f"/{BUCKET_NAME}/{s3_key}"
                r = await client.post(
                    f"{API_BASE}/filespaces/{fs_id}/external/entries",
                    headers=HEADERS,
                    json={
                        "path": ll_path,
                        "kind": "SingleObjectFile",
                        "dataStoreId": ds_id,
                        "singleObjectFileParams": {"objectId": s3_key},
                    },
                )
                return r.status_code, r.text

            results = await asyncio.gather(
                *[do_import(key) for key in batch],
                return_exceptions=True,
            )

            for key, result in zip(batch, results):
                if isinstance(result, Exception):
                    failed += 1
                    print(f"  ERROR  {key}: {result}")
                else:
                    status, body = result
                    if status in (200, 201):
                        created += 1
                    elif status == 409 or (status == 400 and "already" in body.lower()):
                        skipped += 1
                    else:
                        failed += 1
                        print(f"  FAIL   {key}: HTTP {status}")

            # Progress
            done = min(i + batch_size, len(keys))
            print(f"  Progress: {done}/{len(keys)}")
            await asyncio.sleep(0.05)

        print(f"\nDone! Created: {created}, Skipped: {skipped}, Failed: {failed}")


if __name__ == "__main__":
    asyncio.run(main())
```

