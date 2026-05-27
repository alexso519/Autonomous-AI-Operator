"""Localized HTTP error helpers for API routes."""

from __future__ import annotations

from fastapi import HTTPException

from app.config.locale import t


def http_error(status_code: int, key: str, **kwargs) -> HTTPException:
    return HTTPException(status_code=status_code, detail=t(key, **kwargs))
