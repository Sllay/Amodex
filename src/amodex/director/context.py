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
_IDENTIFIER_PART_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|[A-Z]+|\d+"
)
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
    """Tokenize prose plus common source-code identifier conventions.

    ``BillingService`` and ``billing_api`` both expose ``billing`` while keeping
    their full normalized token. This lets an objective reference a source symbol
    and still anchor focused documentation without using unrelated scope paths as
    global context topics.
    """

    result: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        normalized = raw.lower()
        if normalized not in _STOP:
            result.add(normalized)
        for snake_part in raw.split("_"):
            for part in _IDENTIFIER_PART_RE.findall(snake_part):
                lowered = part.lower()
                if len(lowered) >= 3 and lowered not in _STOP:
                    result.add(lowered)
    return result


def _path_tokens(path: str) -> set[str]:
    return _tokens(
        PurePosixPath(path).as_posix().replace("/", " ").replace(".", " ")
    )


def _symbol_tokens(entry: RepositoryEntry) -> set[str]:
    result: set[str] = set()
    for symbol in entry.symbols:
        result.update(_tokens(symbol))
    return result


def _provenance(entry: RepositoryEntry) -> Provenance:
    return Provenance(
        path=entry.path,
        sha256=entry.sha256,
        start_line=1,
        end_line=max(1, entry.line_count),
        authority=entry.authority,
        kind=entry.kind,
    )


def _relevance(entry: RepositoryEntry, *, objective_tokens: set[str]) -> int:
    """Score implicit context strictly from the semantic task objective.

    Exact task read/write paths are already admitted separately. They therefore
    must not contribute their path words to the global relevance vocabulary:
    authorizing ``water_container.gd`` for an inventory task may admit that exact
    file, but it must not implicitly disclose every ``water_*`` source or the
    water architecture. Structural neighbors enter through explicit scope,
    one-hop imports, or adjacent tests rather than token spillover.
    """

    path_overlap = len(_path_tokens(entry.path) & objective_tokens)
    symbol_overlap = len(_symbol_tokens(entry) & objective_tokens)

    if entry.kind in {
        FileKind.DOCUMENTATION,
        FileKind.DECISION,
        FileKind.CONTRACT,
    }:
        if path_overlap == 0 and symbol_overlap == 0:
            return 0
        score = path_overlap * 4 + symbol_overlap * 8
        if entry.safe_text:
            content_overlap = len(
                _tokens(entry.safe_text[:12000]) & objective_tokens
            )
            score += min(6, content_overlap)
        if entry.authority >= DocumentAuthority.FOCUSED_SPEC:
            score += 2
        return score

    score = path_overlap * 4 + symbol_overlap * 8
    if entry.authority >= DocumentAuthority.FOCUSED_SPEC and score:
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

    objective_tokens = _tokens(task.objective)

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
        if (
            entry.path in selected
            or entry.safe_text is None
            or entry.kind == FileKind.SECRET
        ):
            continue
        score = _relevance(entry, objective_tokens=objective_tokens)
        if score <= 0:
            continue
        if entry.kind in {
            FileKind.DOCUMENTATION,
            FileKind.DECISION,
            FileKind.SOURCE,
            FileKind.TEST,
        }:
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
        # High authority is not permission to preload. Focused context is
        # selected only when the semantic objective actually anchors its topic.
        if score >= 4:
            include(entry, f"relevant project fact score={score}")

    selected_paths = list(selected)
    selected_stems = {
        PurePosixPath(path).stem.lower() for path in selected_paths
    }
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
        dotted = (
            entry.path.removeprefix("src/")
            .removesuffix(".py")
            .replace("/", ".")
        )
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
