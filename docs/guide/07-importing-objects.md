# Importing Objects

This is the core operation — creating ExternalEntries that map S3 objects into the filespace.

> **See also:** [Key Concepts — Lazy loading](10-key-concepts.md#lazy-loading), [Path mapping](10-key-concepts.md#path-mapping), [Multiple links](10-key-concepts.md#multiple-links)

## API

```
POST /filespaces/{filespace_id}/external/entries
```

## Request body

```json
{
  "path": "/bucket-name/path/to/file.ext",
  "kind": "SingleObjectFile",
  "dataStoreId": "ds-uuid-1234",
  "singleObjectFileParams": {
    "objectId": "path/to/file.ext"
  }
}
```

| Field | Description |
|---|---|
| `path` | Full target path in the filespace (must start with `/`) |
| `kind` | Must be `"SingleObjectFile"` |
| `dataStoreId` | UUID of the DataStore created in [step 4](04-data-stores.md#create-a-datastore) |
| `singleObjectFileParams.objectId` | The S3 object key (path within the bucket) |

---

## Single file import

### Python

```python
async def import_file(
    filespace_id: str,
    datastore_id: str,
    s3_key: str,
    bucket_name: str,
):
    """
    Import a single S3 object into the filespace.

    The file will appear at: /{bucket_name}/{s3_key}
    For example, S3 key "photos/image.jpg" in bucket "media"
    becomes /media/photos/image.jpg in the filespace.
    """
    ll_path = f"/{bucket_name}/{s3_key}"

    payload = {
        "path": ll_path,
        "kind": "SingleObjectFile",
        "dataStoreId": datastore_id,
        "singleObjectFileParams": {
            "objectId": s3_key,
        },
    }

    response = await client.post(
        f"{API_BASE}/filespaces/{filespace_id}/external/entries",
        headers=HEADERS,
        json=payload,
    )

    if response.status_code in (200, 201):
        print(f"  Imported: {ll_path}")
        return "created"
    elif response.status_code == 409:
        print(f"  Skipped (already exists): {ll_path}")
        return "skipped"
    elif response.status_code == 400:
        body = response.text
        if "already exists" in body.lower():
            print(f"  Skipped (already exists): {ll_path}")
            return "skipped"
        raise Exception(f"Import failed (400): {body[:500]}")
    else:
        raise Exception(f"Import failed ({response.status_code}): {response.text[:500]}")
```

### cURL

```bash
DATASTORE_ID="ds-uuid-1234"
BUCKET="my-bucket-name"
S3_KEY="photos/2025/image.jpg"

curl -s -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     "$API_BASE/filespaces/$FILESPACE_ID/external/entries" \
     -d "{
       \"path\": \"/$BUCKET/$S3_KEY\",
       \"kind\": \"SingleObjectFile\",
       \"dataStoreId\": \"$DATASTORE_ID\",
       \"singleObjectFileParams\": {
         \"objectId\": \"$S3_KEY\"
       }
     }"
```

---

## Batch folder import

Import all S3 objects under a prefix, with automatic folder creation and parallel batching.

> **See also:** [Key Concepts — Rate limiting](10-key-concepts.md#rate-limiting)

### Python

```python
import asyncio

async def import_folder(
    filespace_id: str,
    datastore_id: str,
    bucket_name: str,
    prefix: str,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    batch_size: int = 25,
):
    """
    Import all S3 objects under a prefix into the filespace.
    Creates folder structure automatically.
    """
    # 1. List all objects under the prefix (recursive, no delimiter)
    s3 = boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("/"):  # skip folder markers
                keys.append(key)

    print(f"Found {len(keys)} objects to import")

    # 2. Pre-create all unique folder paths
    unique_dirs = set()
    for key in keys:
        parts = key.rsplit("/", 1)
        if len(parts) > 1:
            unique_dirs.add(parts[0])

    for dir_path in sorted(unique_dirs):
        full_key = f"{bucket_name}/{dir_path}/placeholder"
        await ensure_folder_structure(filespace_id, full_key)

    # 3. Import files in parallel batches
    created = 0
    skipped = 0
    failed = 0

    for i in range(0, len(keys), batch_size):
        batch = keys[i : i + batch_size]
        tasks = [
            import_file(filespace_id, datastore_id, key, bucket_name)
            for key in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                failed += 1
                print(f"  Error: {result}")
            elif result == "created":
                created += 1
            elif result == "skipped":
                skipped += 1

        # Small delay between batches to avoid rate limiting
        await asyncio.sleep(0.05)

    print(f"\nImport complete: {created} created, {skipped} skipped, {failed} failed")
```

