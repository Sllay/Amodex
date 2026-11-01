from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

from .models import (
    DecisionRecord,
    DocumentAuthority,
    FileKind,
    IngestionConflict,
    ProjectContract,
    Provenance,
    RepositoryEntry,
    RepositorySnapshot,
    RuleObservation,
)

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".godot",
}
_SECRET_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
_RULE_RE = re.compile(r"^\s*AMODEX_RULE\s+([A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*$", re.MULTILINE)
_ADR_STATUS_RE = re.compile(r"^\s*Status\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_ADR_SUPERSEDES_RE = re.compile(r"^\s*Supersedes\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_GD_SYMBOL_RE = re.compile(r"^\s*(?:class_name\s+([A-Za-z_]\w*)|func\s+([A-Za-z_]\w*)\s*\()", re.MULTILINE)
_JS_SYMBOL_RE = re.compile(r"\b(?:class|function)\s+([A-Za-z_$][\w$]*)")
_LUAU_SYMBOL_RE = re.compile(
    r"^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)|^\s*local\s+([A-Za-z_]\w*)\s*=\s*{}",
    re.MULTILINE,
)


class IngestionError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_secret(path: PurePosixPath) -> bool:
    name = path.name.lower()
    if name in _SECRET_NAMES:
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return False


def _authority_for(path: PurePosixPath) -> DocumentAuthority:
    lower = path.as_posix().lower()
    if path.as_posix() == "AGENTS.md":
        return DocumentAuthority.ROOT_CONTRACT
    if lower.startswith("harness/context/"):
        return DocumentAuthority.PHASE
    if lower.startswith(("harness/build/", "harness/evidence/")):
        return DocumentAuthority.HISTORICAL
    if "/decisions/" in f"/{lower}" or path.name.lower().startswith("adr-"):
        return DocumentAuthority.ADR
    if any(part in lower for part in ("architecture", "contract", "spec")) and path.suffix.lower() in {".md", ".txt"}:
        return DocumentAuthority.FOCUSED_SPEC
    if any(part in lower for part in ("phase", "season", "milestone", "task")) and path.suffix.lower() in {".md", ".txt"}:
        return DocumentAuthority.PHASE
    if path.name.lower() in {"readme.md", "gdd.md", "overview.md", "project_charter.md"}:
        return DocumentAuthority.OVERVIEW
    if "history" in lower or "archive" in lower or "research/" in lower:
        return DocumentAuthority.HISTORICAL
    if path.suffix.lower() in {".py", ".gd", ".luau", ".lua", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".cs"}:
        return DocumentAuthority.SOURCE
    return DocumentAuthority.UNCLASSIFIED


def _kind_for(path: PurePosixPath) -> FileKind:
    lower = path.as_posix().lower()
    if _is_secret(path):
        return FileKind.SECRET
    if path.as_posix() == "AGENTS.md":
        return FileKind.CONTRACT
    if "/decisions/" in f"/{lower}" or path.name.lower().startswith("adr-"):
        return FileKind.DECISION
    if lower.startswith("tests/") or "/tests/" in f"/{lower}":
        return FileKind.TEST
    if lower.startswith((".github/", "deploy/", "infra/", "scripts/")):
        return FileKind.INFRA
    if path.suffix.lower() in {".md", ".txt", ".rst"}:
        return FileKind.DOCUMENTATION
    if path.suffix.lower() in {".py", ".gd", ".luau", ".lua", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".cs"}:
        return FileKind.SOURCE
    return FileKind.OTHER


def _language_for(path: PurePosixPath) -> str | None:
    return {
        ".py": "python",
        ".gd": "gdscript",
        ".luau": "luau",
        ".lua": "lua",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".cs": "csharp",
    }.get(path.suffix.lower())


def _python_observations(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return (), ()
    symbols: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return tuple(sorted(set(symbols))), tuple(sorted(set(imports)))


def _symbol_observations(path: PurePosixPath, text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    language = _language_for(path)
    if language == "python":
        return _python_observations(text)
    if language == "gdscript":
        symbols = {left or right for left, right in _GD_SYMBOL_RE.findall(text) if left or right}
        return tuple(sorted(symbols)), ()
    if language in {"luau", "lua"}:
        symbols = {left or right for left, right in _LUAU_SYMBOL_RE.findall(text) if left or right}
        return tuple(sorted(symbols)), ()
    if language in {"typescript", "javascript"}:
        return tuple(sorted(set(_JS_SYMBOL_RE.findall(text)))), ()
    return (), ()


def _provenance(entry: RepositoryEntry) -> Provenance:
    return Provenance(
        path=entry.path,
        sha256=entry.sha256,
        start_line=1,
        end_line=max(1, entry.line_count),
        authority=entry.authority,
        kind=entry.kind,
    )


def _decision_from(entry: RepositoryEntry) -> DecisionRecord | None:
    if entry.kind != FileKind.DECISION or not entry.safe_text:
        return None
    first_heading = next(
        (line.lstrip("# ").strip() for line in entry.safe_text.splitlines() if line.startswith("#")),
        Path(entry.path).stem,
    )
    status_match = _ADR_STATUS_RE.search(entry.safe_text)
    status = status_match.group(1).strip().upper() if status_match else "UNKNOWN"
    supersedes_match = _ADR_SUPERSEDES_RE.search(entry.safe_text)
    supersedes = ()
    if supersedes_match:
        supersedes = tuple(
            sorted(
                item.strip()
                for item in re.split(r"[,\s]+", supersedes_match.group(1))
                if item.strip()
            )
        )
    return DecisionRecord(
        decision_id=Path(entry.path).stem,
        title=first_heading,
        status=status,
        source=_provenance(entry),
        supersedes=supersedes,
    )


def ingest_repository(root: Path, *, project_id: str, base_sha: str) -> RepositorySnapshot:
    root = root.resolve()
    if not root.is_dir():
        raise IngestionError(f"repository root does not exist: {root}")

    entries: list[RepositoryEntry] = []
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root)
        if any(part in _IGNORED_DIRS for part in relative.parts[:-1]):
            continue
        rel = PurePosixPath(relative.as_posix())
        kind = _kind_for(rel)
        data = candidate.read_bytes()
        text: str | None = None
        if kind != FileKind.SECRET:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
        line_count = text.count("\n") + 1 if text is not None else 1
        symbols: tuple[str, ...] = ()
        imports: tuple[str, ...] = ()
        if text is not None:
            symbols, imports = _symbol_observations(rel, text)
        entries.append(
            RepositoryEntry(
                path=rel.as_posix(),
                sha256=_sha256(data),
                size_bytes=len(data),
                line_count=line_count,
                kind=kind,
                authority=_authority_for(rel),
                language=_language_for(rel),
                symbols=symbols,
                imports=imports,
                safe_text=text,
            )
        )

    root_contracts = tuple(entry.path for entry in entries if entry.kind == FileKind.CONTRACT)
    rules: dict[str, list[RuleObservation]] = defaultdict(list)
    for entry in entries:
        if not entry.safe_text or entry.authority == DocumentAuthority.UNCLASSIFIED:
            continue
        provenance = _provenance(entry)
        for match in _RULE_RE.finditer(entry.safe_text):
            rules[match.group(1)].append(
                RuleObservation(match.group(1), match.group(2).strip(), provenance)
            )

    effective_rules: dict[str, str] = {}
    conflicts: list[IngestionConflict] = []
    for key, observations in sorted(rules.items()):
        highest = max(item.source.authority for item in observations)
        authoritative = [item for item in observations if item.source.authority == highest]
        values = sorted({item.value for item in authoritative})
        if len(values) > 1:
            conflicts.append(
                IngestionConflict(
                    key=key,
                    competing_values=tuple(values),
                    sources=tuple(item.source for item in authoritative),
                    reason=f"conflicting values at equal authority {highest.name}",
                )
            )
        else:
            effective_rules[key] = values[0]

    decisions = tuple(
        decision
        for entry in entries
        if (decision := _decision_from(entry)) is not None
    )
    project = ProjectContract(
        project_id=project_id,
        base_sha=base_sha,
        root_contracts=root_contracts,
        effective_rules=effective_rules,
        conflicts=tuple(conflicts),
    )
    return RepositorySnapshot(project=project, entries=tuple(entries), decisions=decisions)
