"""Command-line validation of an infrastructure file or directory."""

import argparse
import sys
from collections.abc import Sequence

from .errors import InfrastructureError
from .model import InfrastructureModel


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="infra-model", description="Validate provider-neutral infrastructure YAML.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate", help="validate a YAML file or directory recursively")
    validate.add_argument("path", help="YAML file or directory")
    args = parser.parse_args(argv)
    try:
        report = InfrastructureModel.load(args.path).validate()
    except InfrastructureError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Valid: {report.document_count} documents, {report.entity_count} entities, {len(report.warnings)} warnings")
    return 0
