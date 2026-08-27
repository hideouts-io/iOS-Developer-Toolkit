from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from ios_developer_toolkit.capability_matrix import capability_definitions, probe_capabilities
from ios_developer_toolkit.models import IOSDevice


def emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded iOS Developer Toolkit capability probes")
    parser.add_argument("--pymobiledevice3", required=True)
    parser.add_argument("--identifier", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--product-type", required=True)
    parser.add_argument("--product-version", required=True)
    parser.add_argument("--build-version", required=True)
    parser.add_argument("--connection-type", required=True)
    return parser


def main(arguments: Sequence[str] | None) -> int:
    parsed = build_argument_parser().parse_args(arguments)
    device = IOSDevice(
        identifier=parsed.identifier,
        name=parsed.name,
        product_type=parsed.product_type,
        product_version=parsed.product_version,
        build_version=parsed.build_version,
        connection_type=parsed.connection_type,
    )
    emit({"event": "started", "total": len(capability_definitions())})
    for result in probe_capabilities(Path(parsed.pymobiledevice3), device):
        emit({"event": "result", "result": result.to_mapping()})
    emit({"event": "completed"})
    return 0


if __name__ == "__main__":
    sys.exit(main(None))
