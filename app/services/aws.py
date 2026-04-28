"""
AWS client factory for app-owned AWS calls.

This helper constructs boto3 clients/resources for actions performed by the
connect-manager service itself (e.g. reading from its own SQS queue, writing
metrics, fetching its own secrets). Credentials are resolved by boto3's
default chain — ECS Task Role in prod (Fargate, via the container credential
provider at $AWS_CONTAINER_CREDENTIALS_FULL_URI) or env vars locally — never
passed in explicitly. Customer-owned credentials (per-datastore S3 / SQS
keys decrypted from Postgres) flow through the existing s3_service /
sqs_service modules and are out of scope for this helper.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from botocore.config import Config

logger = logging.getLogger(__name__)

_FORBIDDEN_KWARGS = frozenset(
    {
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
    }
)


def _resolve_region(override: str | None) -> str | None:
    if override is not None:
        return override
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _check_no_explicit_creds(kwargs: dict[str, Any]) -> None:
    bad = _FORBIDDEN_KWARGS & kwargs.keys()
    if bad:
        raise ValueError(
            "app/services/aws.py is for app-owned AWS calls only; "
            "explicit credentials are not allowed (got: "
            f"{sorted(bad)}). Use the customer-credential code path instead."
        )


def get_client(
    service: str,
    *,
    region: str | None = None,
    config: Config | None = None,
    **overrides: Any,
) -> BaseClient:
    """Construct a boto3 client for an app-owned AWS service call.

    Credentials come from boto3's default resolution chain:
      - In prod on EKS: the Pod Identity Agent (169.254.170.23).
      - Locally / in CI: AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY env vars
        (set to "test"/"test" for ministack).

    The endpoint URL is left to boto3, which natively honors AWS_ENDPOINT_URL
    and AWS_ENDPOINT_URL_<SERVICE> — so the same code talks to ministack
    locally and real AWS in prod with no branching here.

    Args:
        service: boto3 service name (e.g. "s3", "sqs", "secretsmanager").
        region: optional region override. Falls back to AWS_REGION /
            AWS_DEFAULT_REGION env vars, then to boto3's own default.
        config: optional botocore Config (retry policy, timeouts, etc.).
        **overrides: any other boto3.client kwarg *except* explicit
            credential kwargs, which are rejected with ValueError.

    Returns:
        A configured boto3 client. The caller is responsible for thread-pool
        offloading if used in async code (see app/services/s3_service.py).
    """
    _check_no_explicit_creds(overrides)

    kwargs: dict[str, Any] = dict(overrides)
    resolved_region = _resolve_region(region)
    if resolved_region is not None:
        kwargs["region_name"] = resolved_region
    if config is not None:
        kwargs["config"] = config

    client = boto3.client(service, **kwargs)
    logger.debug(
        "aws.get_client service=%s endpoint=%s region=%s",
        service,
        client.meta.endpoint_url,
        client.meta.region_name,
    )
    return client


def get_resource(
    service: str,
    *,
    region: str | None = None,
    **overrides: Any,
) -> ServiceResource:
    """Construct a boto3 resource for an app-owned AWS service call.

    Same credential and endpoint rules as get_client. Use a resource only
    when you need the higher-level interface (e.g. DynamoDB Table objects);
    otherwise prefer get_client.
    """
    _check_no_explicit_creds(overrides)

    kwargs: dict[str, Any] = dict(overrides)
    resolved_region = _resolve_region(region)
    if resolved_region is not None:
        kwargs["region_name"] = resolved_region

    resource = boto3.resource(service, **kwargs)
    logger.debug(
        "aws.get_resource service=%s region=%s",
        service,
        resolved_region,
    )
    return resource
