"""Trusted GitHub Actions dispatch boundary for disposable environment runners."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")


class RunnerDispatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GitHubActionsDispatcher:
    repository: str
    token: str
    workflow: str = "environment-runner.yml"
    ref: str = "main"
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not _REPOSITORY_RE.fullmatch(self.repository):
            raise ValueError("repository must be owner/name")
        if not self.token:
            raise ValueError("GitHub dispatch token is required")
        if not _WORKFLOW_RE.fullmatch(self.workflow):
            raise ValueError("workflow must be a YAML filename")
        if not self.ref.strip():
            raise ValueError("workflow ref is required")

    def dispatch(self, *, job_id: str) -> None:
        url = (
            f"https://api.github.com/repos/{self.repository}/actions/workflows/"
            f"{self.workflow}/dispatches"
        )
        try:
            response = httpx.post(
                url,
                headers={
                    "accept": "application/vnd.github+json",
                    "authorization": f"Bearer {self.token}",
                    "x-github-api-version": "2026-03-10",
                },
                json={"ref": self.ref, "inputs": {"job_id": job_id}},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise RunnerDispatchError(f"GitHub Actions dispatch failed: {exc}") from exc
        if response.status_code != 204:
            message = response.text[:1000]
            raise RunnerDispatchError(
                f"GitHub Actions dispatch returned HTTP {response.status_code}: {message}"
            )
