# DataStores

An External DataStore registers an S3 bucket with a filespace. You can list existing DataStores, create new ones, or delete them.

## List DataStores

### API

```
GET /filespaces/{filespace_id}/external/data-stores
```

### Python

```python
async def list_datastores(filespace_id: str):
    response = await client.get(
        f"{API_BASE}/filespaces/{filespace_id}/external/data-stores",
        headers=HEADERS,
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()

    datastores = data if isinstance(data, list) else data.get("data", [])

    for ds in datastores:
        bucket = ds.get("s3StorageParams", {}).get("bucketName", "N/A")
        print(f"  {ds['name']}  (id: {ds['id']}, bucket: {bucket})")

    return datastores
```

### cURL

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     -H "Accept: application/json" \
     "$API_BASE/filespaces/$FILESPACE_ID/external/data-stores" \
     | jq '.data[] | {id, name, bucket: .s3StorageParams.bucketName}'
```

### Response

```json
{
  "data": [
    {
      "id": "ds-uuid-1234",
      "name": "Media Archive",
      "kind": "S3DataStore",
      "s3StorageParams": {
        "bucketName": "my-media-bucket",
        "region": "us-east-1",
        "endpoint": "https://s3.amazonaws.com",
        "useVirtualAddressing": true,
        "urlExpirationMinutes": 10080
      }
    }
  ]
}
```

---

## Create a DataStore

Register an S3 bucket with IAM credentials.

### API

```
POST /filespaces/{filespace_id}/external/data-stores
```

### Request body

```json
{
  "name": "My S3 Datastore",
  "kind": "S3DataStore",
  "s3StorageParams": {
    "bucketName": "my-bucket-name",
    "accessKey": "AKIAIOSFODNN7EXAMPLE",
    "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "region": "us-east-1",
    "endpoint": "https://s3.amazonaws.com",
    "useVirtualAddressing": true,
    "urlExpirationMinutes": 10080
  }
}
```

### Fields

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Display name for the DataStore |
| `kind` | Yes | Must be `"S3DataStore"` |
| `bucketName` | Yes | S3 bucket name |
| `accessKey` | Yes | IAM access key ID |
| `secretKey` | Yes | IAM secret access key |
| `region` | No | AWS region (default: `us-east-1`) |
| `endpoint` | No | Custom S3-compatible endpoint URL |
| `useVirtualAddressing` | No | Virtual-hosted style URLs (default: `true`) |
| `urlExpirationMinutes` | No | Pre-signed URL TTL (default: `10080` = 7 days) |

### Python

```python
async def create_datastore(
    filespace_id: str,
    name: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    endpoint: str = "",
):
    payload = {
        "name": name,
        "kind": "S3DataStore",
        "s3StorageParams": {
            "bucketName": bucket,
            "accessKey": access_key,
            "secretKey": secret_key,
            "region": region,
            "useVirtualAddressing": True,
            "urlExpirationMinutes": 10080,
        },
    }
    if endpoint:
        payload["s3StorageParams"]["endpoint"] = endpoint

    response = await client.post(
        f"{API_BASE}/filespaces/{filespace_id}/external/data-stores",
        headers=HEADERS,
        json=payload,
    )
    response.raise_for_status()
    result = response.json()

    datastore_id = result.get("data", result).get("id")
    print(f"Created DataStore: {datastore_id}")
    return datastore_id
```

### cURL

```bash
curl -s -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     "$API_BASE/filespaces/$FILESPACE_ID/external/data-stores" \
     -d '{
       "name": "My S3 Datastore",
       "kind": "S3DataStore",
       "s3StorageParams": {
         "bucketName": "my-bucket-name",
         "accessKey": "AKIAIOSFODNN7EXAMPLE",
         "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
         "region": "us-east-1",
         "useVirtualAddressing": true,
         "urlExpirationMinutes": 10080
       }
     }' | jq '.data.id'
```

---

## Delete a DataStore

### API

```
DELETE /filespaces/{filespace_id}/external/data-stores/{datastore_id}
```

Returns `200`–`204` on success. Deleting a DataStore does **not** remove the ExternalEntries that reference it — those entries become inaccessible until the DataStore is re-created or entries are cleaned up.

> **See also:** [Key Concepts — DataStore credentials](10-key-concepts.md#datastore-credentials)

