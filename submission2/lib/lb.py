"""Shared Lakebase connection helper for the NorthPeak / Genie Bandits submission.

Resolves the endpoint host + a short-lived OAuth credential through the Databricks
CLI (the pattern documented in the databricks-lakebase skill), then opens a
psycopg2 connection. Uses `hostaddr` (IP resolved via `dig`) alongside `host`
to sidestep the macOS getaddrinfo failure on long Lakebase hostnames, while
still sending `host` for TLS SNI.

Everything is parameterized (profile / project / branch / endpoint / db) so the
same code runs against the dev branch, the throwaway forecast branch, or
production — nothing is hardcoded to one environment.
"""
from __future__ import annotations

import json
import subprocess
import socket
import psycopg2

PROFILE = "rkm-sandbox-1"
PROJECT = "dbdemos-asset-generator"


def _cli(args: list[str]) -> dict | list:
    out = subprocess.check_output(
        ["databricks", *args, "--profile", PROFILE, "-o", "json"],
        text=True,
    )
    return json.loads(out)


def endpoint_path(branch: str, endpoint: str = "primary") -> str:
    return f"projects/{PROJECT}/branches/{branch}/endpoints/{endpoint}"


def host_for(branch: str, endpoint: str = "primary") -> str:
    ep = _cli(["postgres", "get-endpoint", endpoint_path(branch, endpoint)])
    return ep["status"]["hosts"]["host"]


def token_for(branch: str, endpoint: str = "primary") -> str:
    cred = _cli(
        ["postgres", "generate-database-credential", endpoint_path(branch, endpoint)]
    )
    return cred["token"]


def current_user() -> str:
    return _cli(["current-user", "me"])["userName"]


def connect(branch: str, dbname: str = "databricks_postgres", endpoint: str = "primary"):
    host = host_for(branch, endpoint)
    token = token_for(branch, endpoint)
    user = current_user()
    # Resolve to an IP to work around macOS getaddrinfo on long hostnames.
    try:
        hostaddr = socket.gethostbyname(host)
    except OSError:
        hostaddr = subprocess.check_output(
            ["dig", "+short", host], text=True
        ).strip().splitlines()[-1]
    return psycopg2.connect(
        host=host,
        hostaddr=hostaddr,
        port=5432,
        dbname=dbname,
        user=user,
        password=token,
        sslmode="require",
        connect_timeout=30,
    )
