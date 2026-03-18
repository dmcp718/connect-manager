# Folder Structure

Before importing files, ensure the target directory structure exists in the filespace. Folders must be created **top-down** (parent before child) because each creation requires the parent's entry ID.

> **See also:** [Key Concepts — Folder creation order](10-key-concepts.md#folder-creation-order)

## Resolve an entry by path

Returns the entry ID for an existing path.

### API

```
GET /filespaces/{filespace_id}/entries/resolve?path={path}
```

### Python

```python
async def resolve_entry_path(filespace_id: str, path: str) -> str | None:
    """Resolve a filespace path to its entry ID. Returns None if not found."""
    response = await client.get(
        f"{API_BASE}/filespaces/{filespace_id}/entries/resolve",
        headers=HEADERS,
        params={"path": path},
    )
    if response.status_code == 200:
        data = response.json()
        return data.get("data", data).get("id")
    return None
```

---

## Create a folder

### API

```
POST /filespaces/{filespace_id}/entries
```

### Request body

```json
{
  "name": "folder-name",
  "type": "dir",
  "parentId": "parent-entry-uuid"
}
```

### Python

```python
async def create_folder(filespace_id: str, name: str, parent_id: str = None) -> str:
    """Create a folder and return its entry ID."""
    payload = {"name": name, "type": "dir"}
    if parent_id:
        payload["parentId"] = parent_id

    response = await client.post(
        f"{API_BASE}/filespaces/{filespace_id}/entries",
        headers=HEADERS,
        json=payload,
    )
    response.raise_for_status()
    data = response.json()
    return data.get("data", data).get("id")
```

---

## Ensure a full folder path

Given an S3 key, create all intermediate folders that don't already exist.

### Python

```python
async def ensure_folder_structure(filespace_id: str, s3_key: str):
    """
    Given an S3 key like "bucket-name/data/2025/report.csv",
    create the folder tree:  /bucket-name/data/2025/

    Returns the last folder's entry ID.
    """
    parts = s3_key.split("/")
    # Remove the filename (last element)
    folder_parts = parts[:-1] if parts[-1] else parts[:-2]

    current_path = ""
    parent_id = None

    # Resolve root
    root_id = await resolve_entry_path(filespace_id, "/")
    parent_id = root_id

    for folder_name in folder_parts:
        current_path += f"/{folder_name}"

        # Try to resolve existing folder
        existing_id = await resolve_entry_path(filespace_id, current_path)
        if existing_id:
            parent_id = existing_id
            continue

        # Create the folder
        try:
            folder_id = await create_folder(filespace_id, folder_name, parent_id)
            parent_id = folder_id
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                # Race condition — folder was created by another process
                existing_id = await resolve_entry_path(filespace_id, current_path)
                if existing_id:
                    parent_id = existing_id
                    continue
            raise

    return parent_id
```

### cURL

```bash
# Resolve the root entry ID
ROOT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "$API_BASE/filespaces/$FILESPACE_ID/entries/resolve?path=/" \
    | jq -r '.data.id')

# Create a folder at root level
curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "$API_BASE/filespaces/$FILESPACE_ID/entries" \
    -d "{\"name\": \"my-bucket\", \"type\": \"dir\", \"parentId\": \"$ROOT_ID\"}" \
    | jq '.data.id'
```

