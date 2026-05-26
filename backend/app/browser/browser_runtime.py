"""
Browser runtime — Playwright-based automation with httpx fallback.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.browser.page_memory import ActionRecord, PageMemory, PageSnapshot
from app.browser.ui_grounding import UIGrounding

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "browser_screenshots"

# Optional Playwright import
_playwright_available = False
try:
    from playwright.async_api import async_playwright  # type: ignore
    _playwright_available = True
except ImportError:
    async_playwright = None  # type: ignore


@dataclass
class BrowserActionResult:
    action_id: str
    action_type: str
    target: str
    success: bool
    url: str
    title: str
    content: dict[str, Any] = field(default_factory=dict)
    screenshot_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "actionType": self.action_type,
            "target": self.target,
            "success": self.success,
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "screenshotPath": self.screenshot_path,
            "error": self.error,
        }


class BrowserRuntime:
    """Playwright-based browser automation with event streaming."""

    def __init__(
        self,
        execution_id: str,
        *,
        headless: bool = True,
    ) -> None:
        self.execution_id = execution_id
        self.headless = headless
        self.memory = PageMemory(f"browser-{execution_id[:8]}")
        self._playwright = None
        self._browser = None
        self._page = None
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def use_playwright(self) -> bool:
        return _playwright_available

    async def start(self) -> None:
        if not _playwright_available:
            logger.info("Playwright not available; using httpx fallback")
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._page = await self._browser.new_page()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def navigate(
        self,
        url: str,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "BrowserOperator",
    ) -> BrowserActionResult:
        action_id = f"baction-{uuid.uuid4().hex[:8]}"
        success = False
        title = ""
        content: dict[str, Any] = {}
        html = ""
        error: str | None = None

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "browser_action",
                agent,
                f"Navigate to {url}",
                actionId=action_id,
                actionType="navigate",
                target=url,
            )

        try:
            if self._page:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                title = await self._page.title()
                html = await self._page.content()
                success = True
            else:
                html, title = await self._fetch_fallback(url)
                success = bool(html)
        except Exception as exc:
            error = str(exc)
            logger.warning("Navigation failed for %s: %s", url, exc)

        if html:
            grounded = UIGrounding.extract_from_html(html, url)
            content = grounded.to_dict()

        screenshot_path = await self._capture_screenshot(action_id, emit_fn, agent)

        self.memory.record_action(ActionRecord(
            action_id=action_id,
            action_type="navigate",
            target=url,
            value="",
            success=success,
        ))

        if success:
            self.memory.record_snapshot(PageSnapshot(
                snapshot_id=f"snap-{uuid.uuid4().hex[:8]}",
                url=url,
                title=title,
                content_excerpt=content.get("title", "") or html[:300],
                screenshot_path=screenshot_path,
            ))

        return BrowserActionResult(
            action_id=action_id,
            action_type="navigate",
            target=url,
            success=success,
            url=url,
            title=title,
            content=content,
            screenshot_path=screenshot_path,
            error=error,
        )

    async def click(
        self,
        selector: str,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "BrowserOperator",
    ) -> BrowserActionResult:
        action_id = f"baction-{uuid.uuid4().hex[:8]}"

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "browser_action",
                agent,
                f"Click {selector}",
                actionId=action_id,
                actionType="click",
                target=selector,
            )

        success = False
        url = self.memory.current_url
        title = ""
        error: str | None = None

        try:
            if self._page:
                await self._page.click(selector, timeout=10000)
                url = self._page.url
                title = await self._page.title()
                success = True
            else:
                error = "Click requires Playwright; not available in fallback mode"
        except Exception as exc:
            error = str(exc)

        self.memory.record_action(ActionRecord(
            action_id=action_id,
            action_type="click",
            target=selector,
            value="",
            success=success,
        ))

        return BrowserActionResult(
            action_id=action_id,
            action_type="click",
            target=selector,
            success=success,
            url=url,
            title=title,
            error=error,
        )

    async def fill_form(
        self,
        selector: str,
        value: str,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "BrowserOperator",
    ) -> BrowserActionResult:
        action_id = f"baction-{uuid.uuid4().hex[:8]}"

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "browser_action",
                agent,
                f"Fill {selector}",
                actionId=action_id,
                actionType="fill",
                target=selector,
                value=value[:50],
            )

        success = False
        error: str | None = None

        try:
            if self._page:
                await self._page.fill(selector, value, timeout=10000)
                success = True
            else:
                error = "Form fill requires Playwright"
        except Exception as exc:
            error = str(exc)

        self.memory.record_action(ActionRecord(
            action_id=action_id,
            action_type="fill",
            target=selector,
            value=value[:100],
            success=success,
        ))

        return BrowserActionResult(
            action_id=action_id,
            action_type="fill",
            target=selector,
            success=success,
            url=self.memory.current_url,
            title="",
            error=error,
        )

    async def _capture_screenshot(
        self,
        action_id: str,
        emit_fn: EmitFn | None,
        agent: str,
    ) -> str | None:
        path = SCREENSHOT_DIR / f"{self.execution_id[:8]}-{action_id}.png"
        try:
            if self._page:
                await self._page.screenshot(path=str(path))
            else:
                return None

            if emit_fn:
                await emit_fn(
                    self.execution_id,
                    "browser_snapshot",
                    agent,
                    f"Screenshot captured: {path.name}",
                    actionId=action_id,
                    screenshotPath=str(path),
                    provenanceId=f"prov-{action_id}",
                )
            return str(path)
        except Exception as exc:
            logger.warning("Screenshot failed: %s", exc)
            return None

    async def _fetch_fallback(self, url: str) -> tuple[str, str]:
        """httpx-based fallback when Playwright is unavailable."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "AutonomousAI-Operator/1.0"})
            resp.raise_for_status()
            html = resp.text
            title_match = __import__("re").search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            title = title_match.group(1).strip() if title_match else url
            return html, title
