"""
LucidLink API Client Service
Ported from s3_python9_fast.py
"""

import asyncio
from typing import Optional, List, Dict, Any, Union

import httpx

LL_HOST = "https://dev-admin-api.solutions-eng.online:8443/api/v1"


class LucidLinkClient:
    """Async client for LucidLink REST API."""

    def __init__(self):
        self.token: str = ""
        self.filespace_id: str = ""
        self.datastore_id: str = ""
        self.base_url: str = ""

    def configure(self, token: str, filespace_id: str, datastore_id: str) -> None:
        """Configure the client with authentication and IDs."""
        self.token = self._clean_token(token)
        self.filespace_id = filespace_id
        self.datastore_id = datastore_id
        self.base_url = f"{LL_HOST}/filespaces/{self.filespace_id}"

    def _clean_token(self, token: str) -> str:
        """Remove Bearer prefix if present."""
        return token.replace("Bearer ", "").strip()

    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }

    async def list_filespaces(self, token: str) -> Union[List[Dict], str]:
        """List all available filespaces."""
        url = f"{LL_HOST}/filespaces"
        headers = {
            "Authorization": f"Bearer {self._clean_token(token)}",
            "accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict) and "data" in data:
                        return data["data"]
                    return [data]

                return f"ERROR_{resp.status_code}"

        except httpx.TimeoutException:
            return "TIMEOUT: Connection timed out"
        except httpx.ConnectError:
            return "CONN_ERR: Cannot connect to LucidLink API"
        except Exception as e:
            return f"CONN_ERR: {e}"

    async def list_datastores(self, token: str, filespace_id: str) -> List[Dict]:
        """List datastores for a filespace."""
        url = f"{LL_HOST}/filespaces/{filespace_id}/external/data-stores"
        headers = {
            "Authorization": f"Bearer {self._clean_token(token)}",
            "accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    ds_list = data.get("data", data) if isinstance(data, dict) else data
                    if isinstance(ds_list, list):
                        return ds_list

        except Exception:
            pass

        return []

    async def create_s3_datastore(
        self,
        token: str,
        filespace_id: str,
        name: str,
        bucket: str,
        region: str,
        endpoint: Optional[str],
        access_key: str,
        secret_key: str,
    ) -> str:
        """Create a new S3 datastore."""
        url = f"{LL_HOST}/filespaces/{filespace_id}/external/data-stores"
        headers = {
            "Authorization": f"Bearer {self._clean_token(token)}",
            "Content-Type": "application/json",
        }

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

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)

                if resp.status_code in [200, 201]:
                    return "SUCCESS"
                return f"Error {resp.status_code}: {resp.text}"

        except Exception as e:
            return f"Exception: {e}"

    async def get_id_by_path(self, path: str) -> Optional[str]:
        """Get entry ID by path."""
        if not self.base_url:
            return None

        try:
            url = f"{self.base_url}/entries/resolve"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url, headers=self._get_headers(), params={"path": path}
                )

                if resp.status_code == 200:
                    data = resp.json()
                    if "data" in data:
                        return data["data"].get("id")
                    return data.get("id")

        except Exception:
            pass

        return None

    async def create_folder(self, name: str, parent_id: Optional[str]) -> tuple[Optional[str], str]:
        """Create a folder in the filespace. Returns (id, error_message)."""
        if not self.base_url:
            return None, "No base URL configured"

        payload: Dict[str, Any] = {
            "name": name,
            "type": "dir",
        }
        if parent_id:
            payload["parentId"] = parent_id

        try:
            url = f"{self.base_url}/entries"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=self._get_headers(), json=payload)

                if resp.status_code in [200, 201]:
                    data = resp.json()
                    folder_id = data.get("id") or data.get("data", {}).get("id")
                    return folder_id, ""
                elif resp.status_code == 409:
                    return "CONFLICT", ""
                else:
                    return None, f"HTTP {resp.status_code}: {resp.text}"

        except Exception as e:
            return None, str(e)

        return None, "Unknown error"

    async def import_file(self, s3_key: str, ll_path: str) -> tuple[int, str]:
        """Import a file from S3 to LucidLink. Returns (status_code, error_message)."""
        if not self.base_url:
            return 500, "No base URL configured"

        if not self.datastore_id:
            return 500, "No datastore ID configured"

        payload = {
            "path": ll_path,
            "kind": "SingleObjectFile",
            "dataStoreId": self.datastore_id,
            "singleObjectFileParams": {"objectId": s3_key},
        }

        try:
            url = f"{self.base_url}/external/entries"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=self._get_headers(), json=payload)
                error_msg = ""
                if resp.status_code >= 400:
                    try:
                        error_msg = resp.text
                    except Exception:
                        error_msg = f"HTTP {resp.status_code}"
                return resp.status_code, error_msg

        except Exception as e:
            return 500, str(e)

    async def ensure_structure(self, full_key: str) -> tuple[bool, str]:
        """Ensure the folder structure exists for a given key. Returns (success, error_message)."""
        parts = [p for p in full_key.split("/") if p]
        if not full_key.endswith("/"):
            parts.pop()  # Remove filename

        if not parts:
            return True, ""  # No folders to create

        # First, resolve the root folder ID - required as parentId for top-level folders
        root_id = await self.get_id_by_path("/")
        if not root_id:
            return False, "Could not resolve root folder ID"

        curr_id: Optional[str] = root_id
        check_path = ""
        last_error = ""

        for folder in parts:
            check_path += f"/{folder}"
            success = False

            for attempt in range(3):  # Retry up to 3 times
                # First try to resolve existing path
                eid = await self.get_id_by_path(check_path)
                if eid:
                    curr_id = eid
                    success = True
                    break

                # Path doesn't exist, create folder
                nid, error = await self.create_folder(folder, curr_id)
                if nid == "CONFLICT":
                    await asyncio.sleep(0.2)
                    continue
                if nid:
                    curr_id = nid
                    success = True
                    break

                last_error = error or f"Failed to create folder: {folder}"
                await asyncio.sleep(0.5)

            if not success:
                return False, f"Failed to create {check_path}: {last_error}"

        return True, ""
