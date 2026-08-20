import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


def test_wp1_tables_and_gate0_tables_coexist(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    gate0 = {
        "goals",
        "tasks",
        "runs",
        "actions",
        "leases",
        "approvals",
        "outbox",
        "inbox",
        "idempotency_records",
        "audit_records",
    }
    wp1 = {
        "task_dependencies",
        "node_invocations",
        "context_manifests",
        "injection_receipts",
        "gate1_latch_configs",
        "tool_calls",
        "effect_receipts",
        "reconciliations",
    }
    assert gate0 | wp1 <= tables


def test_task_dependencies_have_no_unratified_classification(engine) -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("task_dependencies")}
    assert "dependency_kind" not in columns


def test_injection_receipts_reference_the_exact_manifest_correlation(engine) -> None:
    inspector = inspect(engine)
    foreign_keys = inspector.get_foreign_keys("injection_receipts")
    exact_manifest_fk = next(
        item for item in foreign_keys if item["name"] == "fk_injection_exact_manifest"
    )
    assert exact_manifest_fk["constrained_columns"] == [
        "manifest_context_manifest_id",
        "manifest_invocation_id",
        "manifest_session_id",
        "manifest_transport",
        "manifest_hash",
    ]
    assert exact_manifest_fk["referred_columns"] == [
        "id",
        "invocation_id",
        "session_id",
        "transport",
        "content_hash",
    ]


def test_latch_configuration_rejects_runtime_or_auto_release_semantics(engine) -> None:
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO gate1_latch_configs("
                "config_id,schema_version,host_latch_id,persistence,postgresql_role,"
                "independent_of_llm,independent_of_langgraph,independent_of_openclaw,"
                "guard_before_work_acquisition,guard_before_effect,"
                "health_recovery_releases_latch,production_release,document) VALUES ("
                "'gate1-latch','1.0.0','host:latch:gate1','HOST_PERSISTENT',"
                "'PROJECTION_AND_AUDIT_ONLY',true,true,true,true,true,true,"
                "'DEFERRED_NOT_IMPLEMENTED','{}')"
            )
        )


def test_wp1_database_constraints_reject_unapproved_tool(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO goals(id,title,state) VALUES "
                "('10000000-0000-0000-0000-000000000001','g','ACTIVE')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state) VALUES "
                "('10000000-0000-0000-0000-000000000002',"
                "'10000000-0000-0000-0000-000000000001','t','[\"ok\"]','READY')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO runs(id,task_id,state) VALUES "
                "('10000000-0000-0000-0000-000000000003',"
                "'10000000-0000-0000-0000-000000000002','READY')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO actions(id,run_id,kind,state) VALUES "
                "('10000000-0000-0000-0000-000000000004',"
                "'10000000-0000-0000-0000-000000000003','fixture','READY')"
            )
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO tool_calls(id,operation_id,action_id,run_id,tool_id,"
                    "actor_id,operation_hash,idempotency_key,request) VALUES ("
                    "'10000000-0000-0000-0000-000000000005',"
                    "'10000000-0000-0000-0000-000000000006',"
                    "'10000000-0000-0000-0000-000000000004',"
                    "'10000000-0000-0000-0000-000000000003',"
                    "'host.shell','tester','sha256:' || repeat('a',64),'key','{}')"
                )
            )
