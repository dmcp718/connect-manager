# Filespaces

Retrieve all filespaces associated with your token, then select one for subsequent operations.

## API

```
GET /filespaces
```

## Python

```python
async def list_filespaces():
    response = await client.get(
        f"{API_BASE}/filespaces",
        headers=HEADERS,
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()

    # Response may be a list or wrapped in {"data": [...]}
    filespaces = data if isinstance(data, list) else data.get("data", [])

    for fs in filespaces:
        print(f"  {fs['name']}  (id: {fs['id']})")

    return filespaces
```

## cURL

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     -H "Accept: application/json" \
     "$API_BASE/filespaces" | jq '.data[] | {name, id}'
```

## Response

```json
{
  "data": [
    {
      "id": "a1b2c3d4-5678-9abc-def0-111111111111",
      "name": "production-filespace"
    },
    {
      "id": "b2c3d4e5-6789-abcd-ef01-222222222222",
      "name": "staging-filespace"
    }
  ]
}
```

Save the chosen filespace `id` for all subsequent calls:

```python
FILESPACE_ID = "a1b2c3d4-5678-9abc-def0-111111111111"
```

