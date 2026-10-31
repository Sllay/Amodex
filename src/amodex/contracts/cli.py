"""Small operator/developer CLI for validating executable contract payloads."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .base import canonical_json
from .models import CONTRACT_TYPES


def _load_payload(path: str) -> object:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amodex-contract")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a JSON payload")
    validate.add_argument("contract", choices=sorted(CONTRACT_TYPES))
    validate.add_argument("path", help="JSON file path or '-' for stdin")

    schema = sub.add_parser("schema", help="emit JSON Schema")
    schema.add_argument("contract", choices=sorted(CONTRACT_TYPES))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    model_type = CONTRACT_TYPES[args.contract]

    if args.command == "schema":
        print(json.dumps(model_type.model_json_schema(), indent=2, sort_keys=True))
        return 0

    try:
        model = model_type.model_validate(_load_payload(args.path))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(canonical_json(model))
    return 0
