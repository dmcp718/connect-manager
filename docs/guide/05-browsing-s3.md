# Browsing S3

Before importing, browse the S3 bucket to identify which objects to import. This step uses **boto3 / AWS CLI directly against S3** — it is not a LucidLink API call.

## Python

```python
import boto3

def list_s3_objects(
    bucket: str,
    prefix: str = "",
    access_key: str = "",
    secret_key: str = "",
    region: str = "us-east-1",
    endpoint: str = "",
    max_items: int = 5000,
):
    """
    List S3 objects with folder-like navigation using the '/' delimiter.
    Returns folders (CommonPrefixes) and files (Contents).
    """
    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    client_kwargs = {}
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint

    s3 = session.client("s3", **client_kwargs)

    folders = []
    files = []
    continuation_token = None

    while len(folders) + len(files) < max_items:
        params = {
            "Bucket": bucket,
            "Prefix": prefix,
            "Delimiter": "/",
            "MaxKeys": 1000,
        }
        if continuation_token:
            params["ContinuationToken"] = continuation_token

        resp = s3.list_objects_v2(**params)

        # Folders (common prefixes)
        for cp in resp.get("CommonPrefixes", []):
            folders.append({
                "key": cp["Prefix"],
                "name": cp["Prefix"].rstrip("/").rsplit("/", 1)[-1],
                "type": "folder",
            })

        # Files
        for obj in resp.get("Contents", []):
            if obj["Key"] == prefix:
                continue  # skip the prefix itself
            files.append({
                "key": obj["Key"],
                "name": obj["Key"].rsplit("/", 1)[-1],
                "type": "file",
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })

        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")

    # Sort: folders first (alpha), then files (alpha)
    folders.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())

    return folders + files
```

## AWS CLI

```bash
# List top-level contents
aws s3api list-objects-v2 \
    --bucket my-bucket-name \
    --delimiter "/" \
    --prefix "" \
    --max-keys 100 | jq '{folders: .CommonPrefixes, files: .Contents}'

# Navigate into a folder
aws s3api list-objects-v2 \
    --bucket my-bucket-name \
    --delimiter "/" \
    --prefix "photos/2025/" \
    --max-keys 100
```

