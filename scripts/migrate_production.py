#!/usr/bin/env python3
"""Apply the guarded Amodex production migration set."""

from amodex.deployment.migrations import main


if __name__ == "__main__":
    raise SystemExit(main())
