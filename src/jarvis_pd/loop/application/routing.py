from dataclasses import dataclass
from enum import StrEnum

from jarvis_pd.shared.errors import DomainError, ErrorCode


class Route(StrEnum):
    USER_NODE = "USER_NODE"
    PLANNER_NODE = "PLANNER_NODE"
    AWAIT_APPROVAL = "AWAIT_APPROVAL"
    EXECUTE_ACTION = "EXECUTE_ACTION"
    VERIFY_RESULT = "VERIFY_RESULT"
    PERSIST_RESULT = "PERSIST_RESULT"
    BLOCK_RUN = "BLOCK_RUN"
    FINISH_RUN = "FINISH_RUN"


@dataclass(frozen=True, slots=True)
class RoutingSnapshot:
    run_state: str
    user_recommendation_validated: bool = False
    plan_required: bool = False
    plan_validated: bool = False
    approval_required: bool = False
    approval_valid: bool = False
    action_materialized: bool = False
    result_verified: bool = False
    persistence_required: bool = False
    blocked: bool = False
    completion_satisfied: bool = False


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    route: Route
    reason_code: str


class DeterministicRouter:
    """Pure router: it consumes validated facts and never materializes state."""

    def decide(
        self, snapshot: RoutingSnapshot, *, llm_proposed_route: str | None = None
    ) -> RoutingDecision:
        if llm_proposed_route is not None:
            raise DomainError(
                ErrorCode.UNAUTHORIZED,
                "LLM-proposed route names are data and cannot select execution routes",
            )
        if snapshot.run_state in {"CANCELLED", "SUCCEEDED", "FAILED"}:
            raise DomainError(
                ErrorCode.PRECONDITION_FAILED,
                f"Terminal Run state {snapshot.run_state} has no route",
            )
        if snapshot.run_state == "RECONCILIATION_REQUIRED":
            return RoutingDecision(Route.BLOCK_RUN, "RECONCILE_BEFORE_RETRY")
        if snapshot.run_state in {"CREATED", "READY", "PAUSED", "BLOCKED"}:
            return RoutingDecision(Route.BLOCK_RUN, f"RUN_{snapshot.run_state}")
        if snapshot.run_state != "RUNNING":
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Unknown Run state")
        if snapshot.blocked:
            return RoutingDecision(Route.BLOCK_RUN, "VALIDATED_BLOCKER")
        if snapshot.completion_satisfied:
            return RoutingDecision(Route.FINISH_RUN, "ACCEPTANCE_SATISFIED")
        if not snapshot.user_recommendation_validated:
            return RoutingDecision(Route.USER_NODE, "RECOMMENDATION_REQUIRED")
        if snapshot.plan_required and not snapshot.plan_validated:
            return RoutingDecision(Route.PLANNER_NODE, "PLAN_REQUIRED")
        if snapshot.approval_required and not snapshot.approval_valid:
            return RoutingDecision(Route.AWAIT_APPROVAL, "EXACT_APPROVAL_REQUIRED")
        if not snapshot.action_materialized:
            return RoutingDecision(Route.EXECUTE_ACTION, "ACTION_AUTHORIZED")
        if not snapshot.result_verified:
            return RoutingDecision(Route.VERIFY_RESULT, "VERIFICATION_REQUIRED")
        if snapshot.persistence_required:
            return RoutingDecision(Route.PERSIST_RESULT, "PERSISTENCE_REQUIRED")
        raise DomainError(ErrorCode.PRECONDITION_FAILED, "No registered route matches snapshot")
