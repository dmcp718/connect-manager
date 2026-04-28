"""
ARQ queue-depth metric publisher.

Reads the ARQ work-queue list length from ElastiCache Valkey (TLS + AUTH) and
publishes it as a CloudWatch metric. Driven on a 1-minute EventBridge cadence
from the parent module so the worker ECS service can scale-from-zero on
queue depth — the workers themselves can't publish the metric while scaled
to zero, which is exactly when we need it.

No third-party deps: stdlib socket+ssl speaks just enough RESP for AUTH+LLEN.
"""

from __future__ import annotations

import json
import os
import socket
import ssl

import boto3

_cw = boto3.client("cloudwatch")
_sm = boto3.client("secretsmanager")

_SECRET_ARN = os.environ["VALKEY_SECRET_ARN"]
_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "Connect/ARQ")
_METRIC_NAME = os.environ.get("METRIC_NAME", "connect_arq_queue_depth")
_QUEUE_KEY = os.environ.get("QUEUE_KEY", "arq:queue")
_ENV = os.environ["ENV"]

_secret_cache: tuple[str, int, str] | None = None


def _load_secret() -> tuple[str, int, str]:
    global _secret_cache
    if _secret_cache is None:
        body = json.loads(_sm.get_secret_value(SecretId=_SECRET_ARN)["SecretString"])
        _secret_cache = (body["host"], int(body["port"]), body["auth_token"])
    return _secret_cache


def _resp_bulk(*parts: str) -> bytes:
    out = [f"*{len(parts)}\r\n"]
    for p in parts:
        out.append(f"${len(p)}\r\n{p}\r\n")
    return "".join(out).encode()


def _read_line(sock: ssl.SSLSocket) -> bytes:
    # ElastiCache responses for AUTH/LLEN fit comfortably in a single recv;
    # if we ever issue a multi-bulk reply this will need a real parser.
    buf = b""
    while not buf.endswith(b"\r\n"):
        chunk = sock.recv(64)
        if not chunk:
            raise RuntimeError(f"connection closed (partial: {buf!r})")
        buf += chunk
    return buf


def _llen(host: str, port: int, auth_token: str, key: str) -> int:
    ctx = ssl.create_default_context()
    # ElastiCache TLS uses an internal CA; AUTH is the auth boundary.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with socket.create_connection((host, port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as sock:
            sock.sendall(_resp_bulk("AUTH", "default", auth_token))
            line = _read_line(sock)
            if not line.startswith(b"+OK"):
                raise RuntimeError(f"AUTH failed: {line!r}")

            sock.sendall(_resp_bulk("LLEN", key))
            line = _read_line(sock)
            if not line.startswith(b":"):
                raise RuntimeError(f"LLEN failed: {line!r}")
            return int(line[1:].rstrip(b"\r\n"))


def handler(event, context):
    host, port, token = _load_secret()
    depth = _llen(host, port, token, _QUEUE_KEY)

    _cw.put_metric_data(
        Namespace=_NAMESPACE,
        MetricData=[
            {
                "MetricName": _METRIC_NAME,
                "Dimensions": [
                    {"Name": "Env", "Value": _ENV},
                    {"Name": "Service", "Value": "worker"},
                ],
                "Value": float(depth),
                "Unit": "Count",
            }
        ],
    )
    return {"queue_depth": depth}
