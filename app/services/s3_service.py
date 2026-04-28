"""
S3 Service - Async wrapper for boto3 S3 operations
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, List

import boto3
from botocore.exceptions import ClientError


@dataclass
class S3Item:
    """Represents an S3 object or prefix (folder)."""

    name: str
    key: str
    is_folder: bool
    size: float = 0.0  # Size in MB
    size_formatted: str = ""


@dataclass
class S3ListResult:
    """Result of listing S3 objects with pagination metadata."""

    items: List[S3Item]
    total_count: int
    is_truncated: bool = False


class S3Service:
    """Async S3 service using boto3."""

    def __init__(
        self,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ):
        """Initialize S3 client.

        Args:
            access_key: AWS access key ID
            secret_key: AWS secret access key
            region: AWS region (default us-east-1)
            endpoint_url: Custom S3 endpoint URL (e.g., MinIO, Backblaze B2)
        """
        self._executor = ThreadPoolExecutor(max_workers=4)
        self.endpoint_url = endpoint_url
        self.region = region

        client_kwargs = {
            "region_name": region,
        }

        # Add endpoint URL for custom S3-compatible services
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
            self.client = boto3.client("s3", **client_kwargs)
        else:
            # Use default credentials (environment, ~/.aws/credentials, IAM role)
            self.client = boto3.client("s3", **client_kwargs)

    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous boto3 call in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))

    async def head_bucket(self, bucket: str) -> bool:
        """Check if bucket exists and is accessible."""
        try:
            await self._run_sync(self.client.head_bucket, Bucket=bucket)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                raise ValueError(f"Bucket '{bucket}' not found")
            elif error_code == "403":
                raise ValueError(f"Access denied to bucket '{bucket}'")
            raise ValueError(f"Error accessing bucket: {e}")

    async def list_buckets(self) -> List[str]:
        """List all accessible buckets for the current credentials."""
        try:
            response = await self._run_sync(self.client.list_buckets)
            return [b["Name"] for b in response.get("Buckets", [])]
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "403":
                raise ValueError("Access denied - check your credentials")
            raise ValueError(f"Error listing buckets: {e}")

    async def list_objects(
        self, bucket: str, prefix: str = "", max_items: int = 5000
    ) -> "S3ListResult":
        """List objects in a bucket with a given prefix, handling pagination."""
        folders: List[S3Item] = []
        files: List[S3Item] = []
        is_truncated = False

        try:
            kwargs = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}

            while True:
                response = await self._run_sync(self.client.list_objects_v2, **kwargs)

                # Folders (CommonPrefixes)
                if "CommonPrefixes" in response:
                    for p in response["CommonPrefixes"]:
                        key = p["Prefix"]
                        name = key.rstrip("/").split("/")[-1]
                        folders.append(
                            S3Item(
                                name=name,
                                key=key,
                                is_folder=True,
                            )
                        )

                # Files (Contents)
                if "Contents" in response:
                    for obj in response["Contents"]:
                        key = obj["Key"]
                        if key == prefix:
                            continue
                        name = key.split("/")[-1]
                        size_mb = obj["Size"] / (1024 * 1024)
                        files.append(
                            S3Item(
                                name=name,
                                key=key,
                                is_folder=False,
                                size=size_mb,
                                size_formatted=self._format_size(obj["Size"]),
                            )
                        )

                # Check if we've hit the cap
                if len(folders) + len(files) >= max_items:
                    is_truncated = True
                    break

                # Continue if more results
                if response.get("IsTruncated"):
                    kwargs["ContinuationToken"] = response["NextContinuationToken"]
                else:
                    break

        except ClientError as e:
            raise ValueError(f"Error listing objects: {e}")

        # Sort: folders first (alpha), then files (alpha)
        folders.sort(key=lambda x: x.name.lower())
        files.sort(key=lambda x: x.name.lower())
        items = folders + files

        return S3ListResult(
            items=items,
            total_count=len(items),
            is_truncated=is_truncated,
        )

    async def list_all_objects(self, bucket: str, prefix: str = "") -> List[str]:
        """List all objects recursively (no delimiter)."""
        keys: List[str] = []

        try:
            paginator = self.client.get_paginator("list_objects_v2")

            # Run pagination in thread pool
            def _paginate():
                result = []
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    if "Contents" in page:
                        for obj in page["Contents"]:
                            key = obj["Key"]
                            if not key.endswith("/"):
                                result.append(key)
                return result

            keys = await self._run_sync(_paginate)

        except ClientError as e:
            raise ValueError(f"Error listing objects: {e}")

        return keys

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
