"""
S3 Service - Async wrapper for boto3 S3 operations
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

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


class S3Service:
    """Async S3 service using boto3."""

    def __init__(
        self,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "us-east-1",
    ):
        """Initialize S3 client."""
        self._executor = ThreadPoolExecutor(max_workers=4)

        if access_key and secret_key:
            self.client = boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
        else:
            # Use default credentials (environment, ~/.aws/credentials, IAM role)
            self.client = boto3.client("s3", region_name=region)

    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous boto3 call in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, lambda: func(*args, **kwargs)
        )

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

    async def list_objects(self, bucket: str, prefix: str = "") -> List[S3Item]:
        """List objects in a bucket with a given prefix."""
        items: List[S3Item] = []

        try:
            response = await self._run_sync(
                self.client.list_objects_v2,
                Bucket=bucket,
                Prefix=prefix,
                Delimiter="/",
            )

            # Folders (CommonPrefixes)
            if "CommonPrefixes" in response:
                for p in response["CommonPrefixes"]:
                    key = p["Prefix"]
                    name = key.rstrip("/").split("/")[-1]
                    items.append(S3Item(
                        name=name,
                        key=key,
                        is_folder=True,
                    ))

            # Files (Contents)
            if "Contents" in response:
                for obj in response["Contents"]:
                    key = obj["Key"]
                    # Skip the prefix itself
                    if key == prefix:
                        continue

                    name = key.split("/")[-1]
                    size_mb = obj["Size"] / (1024 * 1024)

                    items.append(S3Item(
                        name=name,
                        key=key,
                        is_folder=False,
                        size=size_mb,
                        size_formatted=self._format_size(obj["Size"]),
                    ))

        except ClientError as e:
            raise ValueError(f"Error listing objects: {e}")

        return items

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
