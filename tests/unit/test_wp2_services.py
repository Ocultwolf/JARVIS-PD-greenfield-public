from datetime import UTC, datetime
from uuid import uuid4

import pytest

from jarvis_pd.contracts.gate1 import AgentInvocation, AgentResultStatus
from jarvis_pd.loop.application.fake_agent_runtime import FakeAgentRuntime, FakeRuntimeResponse
from jarvis_pd.loop.application.policy import Gate1PolicyEvaluator, PolicyInput
from jarvis_pd.loop.application.routing import (
    DeterministicRouter,
    Route,
    RoutingDecision,
    RoutingSnapshot,
)
from jarvis_pd.shared.errors import DomainError, ErrorCode


def test_router_uses_only_validated_state_and_registered_routes() -> None:
    router = DeterministicRouter()
    assert router.decide(RoutingSnapshot(run_state="RUNNING")).route == Route.USER_NODE
    assert (
        router.decide(
            RoutingSnapshot(
                run_state="RUNNING", user_recommendation_validated=True, plan_required=True
            )
        ).route
        == Route.PLANNER_NODE
    )
    assert (
        router.decide(
            RoutingSnapshot(
                run_state="RUNNING",
                user_recommendation_validated=True,
                plan_required=True,
                plan_validated=True,
                approval_required=True,
            )
        ).route
        == Route.AWAIT_APPROVAL
    )


def test_router_rejects_textual_llm_route_even_if_name_is_registered() -> None:
    with pytest.raises(DomainError) as caught:
        DeterministicRouter().decide(
            RoutingSnapshot(run_state="RUNNING"), llm_proposed_route="EXECUTE_ACTION"
        )
    assert caught.value.code == ErrorCode.UNAUTHORIZED


@pytest.mark.parametrize("state", ["CANCELLED", "SUCCEEDED", "FAILED"])
def test_router_rejects_terminal_run_states_despite_executive_guards(state) -> None:
    with pytest.raises(DomainError) as caught:
        DeterministicRouter().decide(
            RoutingSnapshot(
                run_state=state,
                user_recommendation_validated=True,
                plan_validated=True,
                approval_valid=True,
            )
        )
    assert caught.value.code == ErrorCode.PRECONDITION_FAILED


@pytest.mark.parametrize("state", ["CREATED", "READY", "PAUSED", "BLOCKED"])
def test_router_never_returns_execution_for_non_running_states(state) -> None:
    decision = DeterministicRouter().decide(
        RoutingSnapshot(
            run_state=state,
            user_recommendation_validated=True,
            plan_validated=True,
            approval_valid=True,
        )
    )
    assert decision.route == Route.BLOCK_RUN


def test_router_preserves_reconcile_before_retry_over_all_adversarial_guards() -> None:
    decision = DeterministicRouter().decide(
        RoutingSnapshot(
            run_state="RECONCILIATION_REQUIRED",
            user_recommendation_validated=True,
            plan_required=False,
            plan_validated=True,
            approval_required=False,
            approval_valid=True,
            action_materialized=False,
            result_verified=True,
            persistence_required=True,
            completion_satisfied=True,
        )
    )
    assert decision == RoutingDecision(Route.BLOCK_RUN, "RECONCILE_BEFORE_RETRY")


def _allowed_policy(**overrides) -> PolicyInput:
    values = {
        "identity_allowed": True,
        "goal_scope_allowed": True,
        "operation_allowlisted": True,
        "exact_resource_allowed": True,
        "risk_class": "R1",
        "within_limits": True,
        "approval_required": False,
        "approval_exact": False,
        "latch_active": False,
        "outcome_unknown": False,
    }
    values.update(overrides)
    return PolicyInput(**values)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"identity_allowed": False}, "IDENTITY_DENIED"),
        ({"goal_scope_allowed": False}, "SCOPE_DENIED"),
        ({"operation_allowlisted": False}, "OPERATION_DENIED"),
        ({"exact_resource_allowed": False}, "RESOURCE_DENIED"),
        ({"risk_class": "R2"}, "RISK_DENIED"),
        ({"within_limits": False}, "LIMIT_EXCEEDED"),
        ({"latch_active": True}, "LATCH_ACTIVE"),
        ({"outcome_unknown": True}, "RECONCILIATION_REQUIRED"),
        ({"approval_required": True}, "APPROVAL_REQUIRED"),
    ],
)
def test_policy_is_closed_default_deny(change, reason) -> None:
    decision = Gate1PolicyEvaluator().evaluate(_allowed_policy(**change))
    assert not decision.allowed
    assert decision.reason_code == reason


def _invocation() -> AgentInvocation:
    return AgentInvocation(
        contract="jarvis.agent-invocation",
        schema_version="1.0.0",
        invocation_id=uuid4(),
        agent_id="user-node",
        node_run_id=uuid4(),
        run_id=uuid4(),
        message="Return the fixture contract",
        context_injection_id=uuid4(),
        expected_output_contract="jarvis.user-node-output@1.0.0",
        timeout_seconds=60,
        session_policy="FRESH_OR_VERIFIED",
        idempotency_key="fake-runtime-1",
    )


def test_fake_runtime_allows_exactly_one_contract_repair() -> None:
    valid = {
        "contract": "jarvis.user-node-output",
        "schema_version": "1.0.0",
        "proposal_id": str(uuid4()),
        "run_id": str(uuid4()),
        "kind": "NO_ACTION",
        "selected_task_id": None,
        "proposed_tasks": [],
        "rejection_reason": "NO_ELIGIBLE_TASK",
        "explanation": "No eligible work",
    }
    attempts = []

    def responder(invocation, attempt):
        attempts.append(attempt)
        return FakeRuntimeResponse(payload={} if attempt == 1 else valid)

    result = FakeAgentRuntime(responder, lambda: datetime.now(UTC)).invoke(_invocation())
    assert result.status == AgentResultStatus.SUCCEEDED
    assert attempts == [1, 2]


def test_fake_runtime_blocks_after_second_invalid_output() -> None:
    result = FakeAgentRuntime(
        lambda invocation, attempt: FakeRuntimeResponse(payload={}),
        lambda: datetime.now(UTC),
    ).invoke(_invocation())
    assert result.status == AgentResultStatus.INVALID_OUTPUT
    assert result.error_code == "CONTRACT_REPAIR_EXHAUSTED"


def test_fake_runtime_exposes_unavailability_without_fallback_result() -> None:
    result = FakeAgentRuntime(
        lambda invocation, attempt: FakeRuntimeResponse(unavailable_code="RUNTIME_UNAVAILABLE"),
        lambda: datetime.now(UTC),
    ).invoke(_invocation())
    assert result.status == AgentResultStatus.UNAVAILABLE
    assert result.structured_output is None
