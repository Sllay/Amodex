from __future__ import annotations

import re
from pathlib import PurePosixPath

from .models import (
    ContextCapsule,
    ContextSection,
    DocumentAuthority,
    FileKind,
    PlannedTask,
    Provenance,
    RepositoryEntry,
    RepositorySnapshot,
)


class StaleContextError(RuntimeError):
    pass


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "this",
    "that",
    "implement",
    "update",
    "task",
    "tests",
    "test",
    "src",
    "docs",
    # Planning prose appears in almost every architecture document. These words
    # carry too little information to justify disclosure on their own.
    "plan",
    "domain",
    "change",
    "changes",
    "safe",
    "historical",
    "foundation",
    "ownership",
    "system",
    "systems",
    "behavior",
    "requirements",
}


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text) if token.lower() not in _STOP}


def _path_tokens(path: str) -> set[str]:
    return _tokens(PurePosixPath(path).as_posix().replace("/", " ").replace(".", " "))


def _provenance(entry: RepositoryEntry) -> Provenance:
    return Provenance(
        path=entry.path,
        sha256=entry.sha256,
        start_line=1,
        end_line=max(1, entry.line_count),
        authority=entry.authority,
        kind=entry.kind,
    )


def _relevance(entry: RepositoryEntry, task_tokens: set[str]) -> int:
    score = 0
    path_overlap = len(_path_tokens(entry.path) & task_tokens)
    symbol_overlap = len({symbol.lower() for symbol in entry.symbols} & task_tokens)
    score += path_overlap * 4 + symbol_overlap * 8

    content_overlap = 0
    if entry.safe_text and entry.kind in {
        FileKind.DOCUMENTATION,
        FileKind.DECISION,
        FileKind.CONTRACT,
    }:
        sample_tokens = _tokens(entry.safe_text[:12000])
        content_overlap = len(sample_tokens & task_tokens)
        # A path/symbol match may use content as supporting evidence. A document
        # with no structural match must overlap several task-specific concepts;
        # one or two generic words cannot pull an unrelated architecture area in.
        if path_overlap or symbol_overlap:
            score += min(6, content_overlap)
        elif content_overlap >= 3:
            score += min(6, content_overlap)

    if entry.authority >= DocumentAuthority.FOCUSED_SPEC:
        # Authority breaks ties among relevant documents; it must never make an
        # unrelated high-authority document relevant by itself.
        score += 2
    return score


def compile_context(
    snapshot: RepositorySnapshot,
    task: PlannedTask,
    *,
    current_base_sha: str,
    max_document_chars: int = 16_000,
) -> ContextCapsule:
    if current_base_sha != snapshot.project.base_sha:
        raise StaleContextError(
            f"context base {snapshot.project.base_sha} does not match repository {current_base_sha}"
        )

    task_tokens = _tokens(task.objective)
    for path in (*task.write_paths, *task.read_paths):
        task_tokens.update(_path_tokens(path))

    by_path = snapshot.by_path
    selected: dict[str, tuple[RepositoryEntry, str]] = {}

    def include(entry: RepositoryEntry, reason: str) -> None:
        if entry.safe_text is None or entry.kind == FileKind.SECRET:
            return
        selected.setdefault(entry.path, (entry, reason))

    for root_path in snapshot.project.root_contracts:
        if root_path in by_path:
            include(by_path[root_path], "root contract is mandatory authority")

    for path in (*task.write_paths, *task.read_paths):
        entry = by_path.get(path)
        if entry:
            include(entry, "explicit task read/write scope")

    candidates: list[tuple[int, RepositoryEntry]] = []
    for entry in snapshot.entries:
        if entry.path in selected or entry.safe_text is None or entry.kind == FileKind.SECRET:
            continue
        score = _relevance(entry, task_tokens)
        if score <= 0:
            continue
        if entry.kind in {FileKind.DOCUMENTATION, FileKind.DECISION, FileKind.SOURCE, FileKind.TEST}:
            candidates.append((score, entry))

    for score, entry in sorted(
        candidates,
        key=lambda item: (-item[0], -int(item[1].authority), item[1].path),
    ):
        if (
            entry.kind == FileKind.DOCUMENTATION
            and len(entry.safe_text or "") > max_document_chars
            and score < 8
        ):
            continue
        # High authority is not permission to preload. A focused spec or ADR is
        # selected only when the task actually overlaps its path/content/symbols.
        if score >= 4:
            include(entry, f"relevant project fact score={score}")

    selected_paths = list(selected)
    selected_stems = {PurePosixPath(path).stem.lower() for path in selected_paths}
    selected_imports: set[str] = set()
    for path in selected_paths:
        selected_imports.update(by_path[path].imports)
    for entry in snapshot.entries:
        if entry.path in selected or entry.safe_text is None:
            continue
        stem = PurePosixPath(entry.path).stem.lower()
        if entry.kind == FileKind.TEST and any(
            token in stem for token in selected_stems if token
        ):
            include(entry, "test adjacent to selected implementation")
            continue
        dotted = entry.path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        if dotted in selected_imports:
            include(entry, "one-hop imported interface")

    sections: list[ContextSection] = []
    for path, (entry, reason) in sorted(selected.items()):
        sections.append(
            ContextSection(
                path=path,
                reason=reason,
                content=entry.safe_text or "",
                provenance=_provenance(entry),
            )
        )
    excluded = tuple(
        entry.path
        for entry in snapshot.entries
        if entry.safe_text is not None
        and entry.path not in selected
        and entry.kind != FileKind.SECRET
    )
    selected_chars = sum(len(section.content) for section in sections)
    return ContextCapsule(
        project_id=snapshot.project.project_id,
        base_sha=snapshot.project.base_sha,
        task_id=task.task_id,
        sections=tuple(sections),
        excluded_paths=excluded,
        selected_chars=selected_chars,
        naive_full_chars=snapshot.total_context_chars,
    )
