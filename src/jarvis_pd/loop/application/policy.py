from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyInput:
    identity_allowed: bool
    goal_scope_allowed: bool
    operation_allowlisted: bool
    exact_resource_allowed: bool
    risk_class: str
    within_limits: bool
    approval_required: bool
    approval_exact: bool
    latch_active: bool
    outcome_unknown: bool


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason_code: str


class Gate1PolicyEvaluator:
    """Closed G1 policy intersection. It grants no capability by omission."""

    def evaluate(self, value: PolicyInput) -> PolicyDecision:
        checks = (
            (value.identity_allowed, "IDENTITY_DENIED"),
            (value.goal_scope_allowed, "SCOPE_DENIED"),
            (value.operation_allowlisted, "OPERATION_DENIED"),
            (value.exact_resource_allowed, "RESOURCE_DENIED"),
            (value.risk_class in {"R0", "R1"}, "RISK_DENIED"),
            (value.within_limits, "LIMIT_EXCEEDED"),
            (not value.latch_active, "LATCH_ACTIVE"),
            (not value.outcome_unknown, "RECONCILIATION_REQUIRED"),
            (not value.approval_required or value.approval_exact, "APPROVAL_REQUIRED"),
        )
        for accepted, reason in checks:
            if not accepted:
                return PolicyDecision(False, reason)
        return PolicyDecision(True, "POLICY_ALLOWED")
