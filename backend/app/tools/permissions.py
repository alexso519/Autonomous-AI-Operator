import ipaddress
import os
import re
import shlex
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.tools.tool_models import ToolDefinition, ToolExecutionError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_READ_ROOTS = [PROJECT_ROOT]
ALLOWED_WRITE_ROOTS = [PROJECT_ROOT / "data", PROJECT_ROOT / "backend" / "app"]
SHELL_WHITELIST = {
    "echo",
    "pwd",
    "ls",
    "cat",
    "type",
    "whoami",
    "uname",
    "id",
    "date",
}

HTTP_URL_PATTERN = re.compile(r"^https?://[A-Za-z0-9._:-]+(?:/.*)?$")

# Blocked hostnames for outbound fetch/search
_BLOCKED_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
})


def _is_private_ip(hostname: str) -> bool:
    """Return True if hostname resolves to a private/loopback address."""
    try:
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0]
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return True
    except (socket.gaierror, ValueError):
        return False
    return False


def validate_http_get_url(url: str) -> str:
    """Validate a URL for safe read-only HTTP GET requests."""
    if not HTTP_URL_PATTERN.match(url):
        raise ToolExecutionError("URL must be a valid http or https address.")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolExecutionError("Only http and https URLs are allowed.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ToolExecutionError("URL must include a hostname.")

    if hostname in _BLOCKED_HOSTS:
        raise ToolExecutionError("Access to local/private hosts is not allowed.")

    if _is_private_ip(hostname):
        raise ToolExecutionError("Access to private network addresses is not allowed.")

    return url


def _resolve_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        return candidate.resolve(strict=False)
    except Exception:
        raise ToolExecutionError(f"Could not resolve path: {path}")


def _is_within_roots(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            if path == root or path.is_relative_to(root):
                return True
        except ValueError:
            continue
    return False


def validate_read_path(path: str) -> Path:
    resolved = _resolve_path(path)
    if not _is_within_roots(resolved, ALLOWED_READ_ROOTS):
        raise ToolExecutionError("Read path is outside the allowed sandbox.")
    return resolved


def validate_write_path(path: str) -> Path:
    resolved = _resolve_path(path)
    if not _is_within_roots(resolved, ALLOWED_WRITE_ROOTS):
        raise ToolExecutionError("Write path is outside the allowed sandbox.")
    return resolved


def validate_delete_path(path: str) -> Path:
    resolved = _resolve_path(path)
    if not _is_within_roots(resolved, ALLOWED_WRITE_ROOTS):
        raise ToolExecutionError("Delete path is outside the allowed sandbox.")
    return resolved


def validate_shell_command(command: str, args: list[str]) -> None:
    if not command.strip():
        raise ToolExecutionError("Shell command cannot be empty.")

    try:
        parsed = shlex.split(command)
    except ValueError as exc:
        raise ToolExecutionError(f"Invalid shell command syntax: {exc}")

    if not parsed:
        raise ToolExecutionError("Shell command cannot be empty.")

    executable = parsed[0].lower()
    if executable not in SHELL_WHITELIST:
        raise ToolExecutionError(
            f"Shell command '{executable}' is not in the safe allowlist."
        )

    for arg in args:
        if any(ch in arg for ch in [";", "&&", "||", "|", "$", "`", "\\"]):
            raise ToolExecutionError("Shell arguments contain prohibited characters.")


def validate_http_post_url(url: str) -> None:
    if not HTTP_URL_PATTERN.match(url):
        raise ToolExecutionError("HTTP POST URL must be a valid http or https address.")


def validate_tool_request(definition: ToolDefinition, input_data: Any) -> None:
    name = definition.name
    if name == "shell_command":
        validate_shell_command(input_data.command, input_data.args)
    elif name == "file_reader":
        validate_read_path(input_data.path)
    elif name == "file_write":
        validate_write_path(input_data.path)
    elif name == "file_delete":
        validate_delete_path(input_data.path)
    elif name == "http_post":
        validate_http_post_url(input_data.url)
    elif name == "json_transform":
        # No external side effects; schema validation is sufficient.
        pass
    elif name in {"calculator", "markdown_generator", "structured_data_extractor"}:
        pass
    elif name == "web_search":
        if not input_data.query.strip():
            raise ToolExecutionError("Search query cannot be empty.")
    elif name == "webpage_fetch":
        validate_http_get_url(input_data.url)
    else:
        raise ToolExecutionError(f"No specific validation rule for tool '{name}'.")
