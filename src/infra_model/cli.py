"""Command-line validation and offline provider rendering."""

import argparse
import sys
from collections.abc import Sequence

import yaml

from .adapters.fabric import FabricAdapterConfig, render_fabric
from .errors import InfrastructureError
from .model import InfrastructureModel


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="infra-model", description="Validate or render provider-neutral infrastructure YAML.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate", help="validate a YAML file or directory recursively")
    validate.add_argument("path", help="YAML file or directory")
    render = subcommands.add_parser("render", help="render a provider document offline")
    providers = render.add_subparsers(dest="provider", required=True)
    fabric = providers.add_parser("fabric", help="render historical fabric-generic-cluster YAML")
    fabric.add_argument("path", metavar="INPUT", help="generic model YAML file or directory")
    fabric.add_argument("--config", required=True, help="FABRIC adapter configuration file")
    fabric.add_argument("--cluster", help="Cluster metadata name or canonical reference")
    args = parser.parse_args(argv)
    try:
        model = InfrastructureModel.load(args.path)
        report = model.validate()
        if args.command == "render":
            config = FabricAdapterConfig.from_file(args.config)
            document = render_fabric(model, cluster=args.cluster, config=config)
            output = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    except InfrastructureError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.command == "render":
        print(output, end="")
    else:
        print(f"Valid: {report.document_count} documents, {report.entity_count} entities, {len(report.warnings)} warnings")
    return 0
