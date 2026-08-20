import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from jarvis_pd.contracts.gate1 import ApprovalDecision, ApprovalRequest
from jarvis_pd.contracts.registry import CONTRACTS
from jarvis_pd.loop.application.approval_service import ApprovalService
from jarvis_pd.persistence.db import build_engine
from jarvis_pd.persistence.uow import UnitOfWork
from jarvis_pd.registry import load_registries
from jarvis_pd.shared.clock import SystemClock
from jarvis_pd.shared.errors import DomainError


def main() -> int:
    parser = argparse.ArgumentParser(prog="jarvis")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("contracts")
    validate.add_argument("action", choices=["validate", "schema"])
    validate.add_argument("--fixture", type=Path)
    registry = sub.add_parser("registry")
    registry.add_argument("action", choices=["validate"])
    registry.add_argument("--config-dir", type=Path, default=Path("config"))
    approval = sub.add_parser("approval")
    approval.add_argument("action", choices=["request", "decide"])
    approval.add_argument("--document", type=Path, required=True)
    approval.add_argument("--request-id", type=UUID, required=True)
    args = parser.parse_args()

    try:
        if args.command == "approval":
            dsn = os.environ.get("JARVIS_DATABASE_DSN")
            actor = os.environ.get("JARVIS_AUTHENTICATED_ACTOR")
            if not dsn or not actor:
                raise ValueError("JARVIS_DATABASE_DSN and JARVIS_AUTHENTICATED_ACTOR are required")
            payload = json.loads(args.document.read_text())
            engine = build_engine(dsn)
            service = ApprovalService(lambda: UnitOfWork(engine), SystemClock())
            if args.action == "request":
                service.request(
                    ApprovalRequest.model_validate(payload),
                    requested_by=actor,
                    request_id=args.request_id,
                )
            else:
                service.decide(
                    ApprovalDecision.model_validate(payload),
                    authenticated_actor_id=actor,
                    request_id=args.request_id,
                )
        elif args.command == "registry":
            load_registries(args.config_dir)
        elif args.action == "schema":
            schema = CONTRACTS["jarvis.user-node-output@1.0.0"].model_json_schema()
            print(json.dumps(schema, indent=2))
        else:
            if args.fixture is None:
                parser.error("--fixture is required for contracts validate")
            data = json.loads(args.fixture.read_text())
            CONTRACTS["jarvis.user-node-output@1.0.0"].model_validate(data)
    except (OSError, ValueError, ValidationError, json.JSONDecodeError, DomainError) as error:
        print(f"INVALID: {error}")
        return 2
    print("VALID")
    return 0
