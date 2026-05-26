"""
UI grounding — extract structured content from pages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredPageContent:
    title: str
    headings: list[str]
    links: list[dict[str, str]]
    paragraphs: list[str]
    forms: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "headings": self.headings[:20],
            "links": self.links[:30],
            "paragraphs": self.paragraphs[:15],
            "forms": self.forms[:5],
            "metadata": self.metadata,
        }


class UIGrounding:
    """Extract structured page content from HTML or text."""

    HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
    LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
    PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)
    FORM_RE = re.compile(r"<form[^>]*>(.*?)</form>", re.I | re.S)
    INPUT_RE = re.compile(r'<input[^>]+name=["\']([^"\']+)["\']', re.I)
    TAG_RE = re.compile(r"<[^>]+>")

    @classmethod
    def extract_from_html(cls, html: str, url: str = "") -> StructuredPageContent:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = cls._strip_tags(title_match.group(1)) if title_match else ""

        headings = [
            cls._strip_tags(h)[:200]
            for h in cls.HEADING_RE.findall(html)
            if cls._strip_tags(h).strip()
        ]

        links = []
        for href, text in cls.LINK_RE.findall(html):
            text_clean = cls._strip_tags(text).strip()[:100]
            if text_clean or href:
                links.append({"href": href[:500], "text": text_clean})

        paragraphs = [
            cls._strip_tags(p).strip()[:500]
            for p in cls.PARA_RE.findall(html)
            if len(cls._strip_tags(p).strip()) > 20
        ]

        forms = []
        for form_html in cls.FORM_RE.findall(html):
            inputs = cls.INPUT_RE.findall(form_html)
            if inputs:
                forms.append({"inputs": inputs[:10]})

        return StructuredPageContent(
            title=title,
            headings=headings,
            links=links,
            paragraphs=paragraphs,
            forms=forms,
            metadata={"url": url, "htmlLength": len(html)},
        )

    @classmethod
    def extract_from_text(cls, text: str, url: str = "") -> StructuredPageContent:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        headings = [l for l in lines if l.startswith("#") or (len(l) < 80 and l.isupper())][:10]
        paragraphs = [l for l in lines if len(l) > 40][:15]
        return StructuredPageContent(
            title=lines[0][:100] if lines else "",
            headings=headings,
            links=[],
            paragraphs=paragraphs,
            forms=[],
            metadata={"url": url, "textLength": len(text)},
        )

    @classmethod
    def _strip_tags(cls, text: str) -> str:
        return cls.TAG_RE.sub("", text).strip()
