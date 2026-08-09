"""Shared helpers for building ``azure.functions.HttpRequest`` objects in tests."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

import azure.functions as func


def make_request(
    *,
    method: str,
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, str]] = None,
    route_params: Optional[Mapping[str, str]] = None,
    json_body: Optional[dict[str, Any]] = None,
    body: bytes = b"",
) -> func.HttpRequest:
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers = {**(headers or {}), "Content-Type": "application/json"}
    return func.HttpRequest(
        method=method,
        url=url,
        headers=headers or {},
        params=params or {},
        route_params=route_params or {},
        body=body,
    )
