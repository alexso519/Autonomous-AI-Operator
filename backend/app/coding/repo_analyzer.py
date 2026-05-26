"""
Repository analyzer — inspect structure, map dependencies, index codebase.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".md"}
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".pytest_cache",
    "venv", ".venv", "dist", "build", ".next",
}
TEST_PATTERNS = re.compile(r"(test_|_test\.|\.test\.|spec\.)", re.I)


@dataclass
class FileEntry:
    path: str
    relative_path: str
    language: str
    size_bytes: int
    is_test: bool
    imports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relativePath": self.relative_path,
            "language": self.language,
            "sizeBytes": self.size_bytes,
            "isTest": self.is_test,
            "imports": self.imports,
        }


@dataclass
class RepoAnalysis:
    root: str
    total_files: int
    code_files: int
    test_files: int
    languages: dict[str, int]
    files: list[FileEntry]
    dependency_graph: dict[str, list[str]]
    test_paths: list[str]
    relevant_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "totalFiles": self.total_files,
            "codeFiles": self.code_files,
            "testFiles": self.test_files,
            "languages": self.languages,
            "files": [f.to_dict() for f in self.files[:100]],
            "dependencyGraph": self.dependency_graph,
            "testPaths": self.test_paths,
            "relevantFiles": self.relevant_files,
        }


class RepoAnalyzer:
    """Analyze repository structure and build dependency map."""

    @classmethod
    def analyze(
        cls,
        root: Path | str,
        *,
        task_hint: str = "",
        max_files: int = 500,
    ) -> RepoAnalysis:
        root_path = Path(root).resolve()
        if not root_path.exists():
            raise FileNotFoundError(f"Repository path not found: {root_path}")

        files: list[FileEntry] = []
        languages: dict[str, int] = {}
        dependency_graph: dict[str, list[str]] = {}
        test_paths: list[str] = []

        for item in root_path.rglob("*"):
            if cls._should_skip(item, root_path):
                continue
            if not item.is_file():
                continue
            if len(files) >= max_files:
                break

            rel = str(item.relative_to(root_path)).replace("\\", "/")
            ext = item.suffix.lower()
            if ext not in CODE_EXTENSIONS and ext not in {".json", ".yaml", ".yml", ".toml"}:
                continue

            lang = cls._detect_language(ext)
            languages[lang] = languages.get(lang, 0) + 1
            is_test = bool(TEST_PATTERNS.search(rel))
            imports = cls._extract_imports(item, lang)

            entry = FileEntry(
                path=str(item),
                relative_path=rel,
                language=lang,
                size_bytes=item.stat().st_size,
                is_test=is_test,
                imports=imports,
            )
            files.append(entry)
            if imports:
                dependency_graph[rel] = imports
            if is_test:
                test_paths.append(rel)

        relevant = cls._rank_relevant_files(files, task_hint)

        return RepoAnalysis(
            root=str(root_path),
            total_files=len(files),
            code_files=sum(1 for f in files if not f.is_test),
            test_files=sum(1 for f in files if f.is_test),
            languages=languages,
            files=files,
            dependency_graph=dependency_graph,
            test_paths=test_paths,
            relevant_files=relevant,
        )

    @classmethod
    def _should_skip(cls, path: Path, root: Path) -> bool:
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            return True
        return any(part in SKIP_DIRS for part in rel_parts)

    @classmethod
    def _detect_language(cls, ext: str) -> str:
        return {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".md": "markdown",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
        }.get(ext, "unknown")

    @classmethod
    def _extract_imports(cls, path: Path, lang: str) -> list[str]:
        if lang != "python":
            return []
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            return []

        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        return imports[:20]

    @classmethod
    def _rank_relevant_files(
        cls,
        files: list[FileEntry],
        task_hint: str,
    ) -> list[str]:
        hint_tokens = set(re.findall(r"\w+", task_hint.lower()))
        scored: list[tuple[float, str]] = []

        for f in files:
            score = 0.0
            rel_lower = f.relative_path.lower()
            if f.is_test and any(k in hint_tokens for k in ("test", "fix", "fail", "debug")):
                score += 3.0
            if f.language == "python":
                score += 0.5
            for token in hint_tokens:
                if token in rel_lower:
                    score += 1.5
            if "backend" in rel_lower or "app" in rel_lower:
                score += 0.3
            scored.append((score, f.relative_path))

        scored.sort(key=lambda x: (-x[0], x[1]))
        return [p for s, p in scored if s > 0][:15] or [f.relative_path for f in files[:10]]
