import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, text

from jarvis_pd.contracts.gate1 import (
    ApprovalDecision,
    ApprovalRequest,
    EffectReceipt,
    SandboxLimits,
    ToolCallOutcome,
    ToolCallRequest,
)
from jarvis_pd.loop.application.approval_service import ApprovalService
from jarvis_pd.loop.application.fixture_atomic_write import FixtureAtomicWrite
from jarvis_pd.loop.application.host_latch import (
    EffectiveHostCredentialProvider,
    HostLatch,
    HostLatchAuthority,
    HostProcessCredentials,
    StaticHostCredentialProvider,
)
from jarvis_pd.loop.application.integrity_service import uow_factory
from jarvis_pd.loop.application.selection_service import SelectionService
from jarvis_pd.loop.application.tool_effect_service import ToolEffectService, operation_hash
from jarvis_pd.loop.application.worker_continuity import (
    LocalWorker,
    WorkerContinuityService,
    WorkerExecutionError,
    WorkerRun,
)
from jarvis_pd.shared.errors import DomainError, ErrorCode


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, value: timedelta) -> None:
        self.value += value


def _latch_document(*, active: bool, reason: str, revision: int) -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0.0",
            "latch_id": "gate1-local",
            "active": active,
            "reason": reason,
            "revision": revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _set_latch(
    engine, path: Path, clock: MutableClock, *, active: bool, reason: str, revision: int
) -> None:
    path.parent.chmod(0o750)
    if path.exists():
        path.chmod(0o640)
    raw = _latch_document(active=active, reason=reason, revision=revision)
    path.write_bytes(raw)
    path.chmod(0o440)
    path.parent.chmod(0o550)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO gate1_local_latch"
                "(latch_id,active,reason,revision,host_state_hash,observed_at) "
                "VALUES ('gate1-local',:active,:reason,:revision,:digest,:now) "
                "ON CONFLICT(latch_id) DO UPDATE SET active=EXCLUDED.active,"
                "reason=EXCLUDED.reason,revision=EXCLUDED.revision,"
                "host_state_hash=EXCLUDED.host_state_hash,observed_at=EXCLUDED.observed_at"
            ),
            {
                "active": active,
                "reason": reason,
                "revision": revision,
                "digest": digest,
                "now": clock.now(),
            },
        )


@pytest.fixture
def wp4(engine, tmp_path):
    clock = MutableClock()
    authority_dir = tmp_path / "human-owner-latch"
    authority_dir.mkdir(mode=0o750)
    path = authority_dir / "gate1-local.json"
    _set_latch(engine, path, clock, active=False, reason="acceptance-fixture", revision=1)
    worker = HostProcessCredentials.unprivileged(os.getuid() + 10000, os.getgid(), frozenset())
    authority = HostLatchAuthority(
        authority_uid=os.getuid(),
        read_gid=os.getgid(),
        worker_uid=worker.uid,
        worker_primary_gid=worker.primary_gid,
        worker_supplementary_gids=worker.supplementary_gids,
        trusted_root=authority_dir,
    )
    service = WorkerContinuityService(
        uow_factory(engine), clock, HostLatch(path, authority, StaticHostCredentialProvider(worker))
    )
    return clock, service, path, authority


def _host_latch(path: Path, authority: HostLatchAuthority) -> HostLatch:
    return HostLatch(
        path,
        authority,
        StaticHostCredentialProvider(authority.expected_worker),
    )


def _ready_task(engine) -> tuple[UUID, UUID]:
    goal_id, task_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO goals(id,title,state) VALUES (:id,'G','ACTIVE')"), {"id": goal_id}
        )
        connection.execute(
            text(
                "INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state,priority) "
                "VALUES (:id,:goal,'T',CAST(:criteria AS jsonb),'READY',1)"
            ),
            {"id": task_id, "goal": goal_id, "criteria": json.dumps(["done"])},
        )
    return goal_id, task_id


def _running(engine, clock: MutableClock, *, expired: bool = False) -> WorkerRun:
    _, task_id = _ready_task(engine)
    run_id = uuid4()
    expires = clock.now() - timedelta(seconds=1) if expired else clock.now() + timedelta(seconds=30)
    with engine.begin() as connection:
        connection.execute(text("UPDATE tasks SET state='RUNNING' WHERE id=:id"), {"id": task_id})
        connection.execute(
            text("INSERT INTO runs(id,task_id,state,fencing_token) VALUES (:id,:task,'RUNNING',1)"),
            {"id": run_id, "task": task_id},
        )
        connection.execute(
            text(
                "INSERT INTO leases(resource_type,resource_id,owner_id,fencing_token,state,expires_at) "
                "VALUES ('Task',:task,'owner-1',1,'ACTIVE',:expires)"
            ),
            {"task": task_id, "expires": expires},
        )
        connection.execute(
            text(
                "INSERT INTO worker_heartbeats(worker_id,owner_id,status,last_seen_at,expires_at) "
                "VALUES (:worker,'owner-1','RUNNING',:now,:expires)"
            ),
            {
                "worker": f"worker-{run_id}",
                "now": clock.now() - timedelta(seconds=2),
                "expires": expires,
            },
        )
    return WorkerRun(task_id, run_id, "owner-1", 1)


def _tool_request(
    run: WorkerRun, action_id: UUID, approval_id: UUID, content: bytes
) -> ToolCallRequest:
    request = ToolCallRequest(
        contract="jarvis.tool-call-request",
        schema_version="1.0.0",
        tool_call_id=uuid4(),
        operation_id=uuid4(),
        action_id=action_id,
        run_id=run.run_id,
        tool_id="fixture.atomic_write",
        actor_id=run.owner_id,
        target_relative_path="result/output.txt",
        content_hash=FixtureAtomicWrite.digest(content),
        operation_hash="sha256:" + "0" * 64,
        idempotency_key=f"effect-{uuid4()}",
        approval_id=approval_id,
        risk_class="R1",
        effect_class="COMPENSATABLE",
        sandbox=SandboxLimits(
            network_enabled=False,
            allowed_operations=["fixture.atomic_write"],
            max_files=1,
            max_bytes=4096,
            timeout_seconds=30,
        ),
    )
    return request.model_copy(update={"operation_hash": operation_hash(request)})


def _approve(engine, clock: MutableClock, request: ToolCallRequest) -> None:
    approval = ApprovalRequest(
        contract="jarvis.approval-request",
        schema_version="1.0.0",
        approval_request_id=request.approval_id,
        operation_id=request.operation_id,
        operation_hash=request.operation_hash,
        requested_from="human-owner",
        requested_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=30),
        max_uses=1,
        status="PENDING",
    )
    service = ApprovalService(uow_factory(engine), clock)
    service.request(approval, requested_by=request.actor_id, request_id=uuid4())
    service.decide(
        ApprovalDecision(
            contract="jarvis.approval-decision",
            schema_version="1.0.0",
            approval_request_id=request.approval_id,
            operation_hash=request.operation_hash,
            decision="APPROVED",
            decided_by="human-owner",
            decided_at=clock.now(),
            decision_reason="WP4 canonical-result acceptance",
        ),
        authenticated_actor_id="human-owner",
        request_id=uuid4(),
    )


def _real_receipt(
    engine,
    clock: MutableClock,
    run: WorkerRun,
    sandbox: Path,
    *,
    failed: bool = False,
) -> EffectReceipt:
    action_id, approval_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO actions(id,run_id,kind,state) VALUES (:id,:run,'fixture.atomic_write','AUTHORIZED')"
            ),
            {"id": action_id, "run": run.run_id},
        )
        goal_id = connection.execute(
            text("SELECT t.goal_id FROM tasks t JOIN runs r ON r.task_id=t.id WHERE r.id=:run"),
            {"run": run.run_id},
        ).scalar_one()
    content = b"canonical-effect"
    request = _tool_request(run, action_id, approval_id, content)
    _approve(engine, clock, request)
    (sandbox / "result").mkdir(parents=True)

    def fault(phase: str) -> None:
        if failed and phase == "before_temp_create":
            raise OSError("demonstrated-before-dispatch")

    service = ToolEffectService(
        uow_factory(engine),
        clock,
        FixtureAtomicWrite(sandbox, fault if failed else None),
        lambda: False,
    )
    return service.execute(
        request,
        content,
        owner_id=run.owner_id,
        fencing_token=run.fencing_token,
        authorized_goal_id=goal_id,
        request_id=uuid4(),
    )


def _states(engine, run: WorkerRun) -> tuple[str, str, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT r.state,t.state,l.state FROM runs r JOIN tasks t ON t.id=r.task_id "
                "JOIN leases l ON l.resource_id=t.id WHERE r.id=:run"
            ),
            {"run": run.run_id},
        ).one()
    return tuple(row)


def test_host_latch_persists_and_rejects_absence_corruption_and_projection_divergence(
    engine, wp4
) -> None:
    clock, service, path, authority = wp4
    service.require_latch_clear()
    WorkerContinuityService(
        uow_factory(engine), clock, _host_latch(path, authority)
    ).require_latch_clear()
    path.parent.chmod(0o750)
    path.unlink()
    path.parent.chmod(0o550)
    with pytest.raises(DomainError, match="HOST_LATCH_UNREADABLE"):
        service.require_latch_clear()
    _set_latch(engine, path, clock, active=False, reason="restored", revision=2)
    path.parent.chmod(0o750)
    path.chmod(0o640)
    path.write_text("corrupt")
    path.chmod(0o440)
    path.parent.chmod(0o550)
    with pytest.raises(DomainError, match="HOST_LATCH_INVALID_JSON"):
        service.require_latch_clear()
    _set_latch(engine, path, clock, active=False, reason="host", revision=3)
    with engine.begin() as connection:
        connection.execute(text("UPDATE gate1_local_latch SET reason='diverged'"))
    with pytest.raises(DomainError, match="LATCH_PROJECTION_DIVERGED"):
        service.require_latch_clear()


def test_host_latch_rejects_wrong_identity_and_insecure_permissions(engine, wp4) -> None:
    _, service, path, authority = wp4
    with pytest.raises(DomainError, match="HOST_LATCH_AUTHORITY_MISMATCH"):
        _host_latch(
            path,
            HostLatchAuthority(
                authority_uid=os.getuid() + 1,
                read_gid=os.getgid(),
                worker_uid=authority.worker_uid,
                worker_primary_gid=authority.worker_primary_gid,
                worker_supplementary_gids=authority.worker_supplementary_gids,
                trusted_root=authority.trusted_root,
            ),
        ).read()
    path.chmod(0o660)
    with pytest.raises(DomainError, match="HOST_LATCH_INSECURE_PERMISSIONS"):
        service.require_latch_clear()
    path.chmod(0o440)
    path.parent.chmod(0o770)
    with pytest.raises(DomainError, match="HOST_LATCH_INSECURE_PERMISSIONS"):
        service.require_latch_clear()


def test_host_latch_rejects_final_and_parent_symlinks(tmp_path, wp4) -> None:
    _, _, path, authority = wp4
    path.parent.chmod(0o750)
    link = path.parent / "link.json"
    link.symlink_to(path)
    path.parent.chmod(0o550)
    with pytest.raises(DomainError, match="HOST_LATCH_INVALID_FILE_TYPE"):
        _host_latch(link, authority).read()
    real_parent = tmp_path / "real-authority"
    real_parent.mkdir(mode=0o550)
    path.parent.chmod(0o750)
    parent_link = path.parent / "authority-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    path.parent.chmod(0o550)
    with pytest.raises(DomainError, match="HOST_LATCH_INVALID_FILE_TYPE"):
        _host_latch(parent_link / "gate1.json", authority).read()


def test_host_latch_rejects_v3_same_uid_before_work_acquisition(engine, wp4) -> None:
    clock, _, path, authority = wp4
    same_identity = HostProcessCredentials.unprivileged(
        authority.authority_uid,
        authority.worker_primary_gid,
        authority.worker_supplementary_gids,
    )
    unsafe_authority = HostLatchAuthority(
        authority_uid=authority.authority_uid,
        read_gid=authority.read_gid,
        worker_uid=same_identity.uid,
        worker_primary_gid=same_identity.primary_gid,
        worker_supplementary_gids=same_identity.supplementary_gids,
        trusted_root=authority.trusted_root,
    )
    service = WorkerContinuityService(
        uow_factory(engine),
        clock,
        HostLatch(path, unsafe_authority, StaticHostCredentialProvider(same_identity)),
    )
    with pytest.raises(DomainError, match="HOST_LATCH_AUTHORITY_NOT_SEPARATED"):
        service.require_latch_clear()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0


def test_host_latch_accepts_distinct_read_only_worker(wp4) -> None:
    _, _, path, authority = wp4
    assert _host_latch(path, authority).read().active is False
    assert not any(
        hasattr(HostLatch, operation)
        for operation in ("create", "write", "replace", "chmod", "deactivate", "release")
    )


@pytest.mark.parametrize("permission", [0o460, 0o446])
def test_host_latch_rejects_group_or_global_write(wp4, permission) -> None:
    _, service, path, _ = wp4
    path.chmod(permission)
    with pytest.raises(DomainError, match="HOST_LATCH_INSECURE_PERMISSIONS"):
        service.require_latch_clear()


def test_host_latch_rejects_worker_owner_and_primary_or_supplementary_group_write(wp4) -> None:
    _, _, path, authority = wp4
    worker_owner = HostLatchAuthority(
        authority_uid=authority.worker_uid,
        read_gid=authority.read_gid,
        worker_uid=authority.worker_uid,
        worker_primary_gid=authority.worker_primary_gid,
        worker_supplementary_gids=authority.worker_supplementary_gids,
        trusted_root=authority.trusted_root,
    )
    with pytest.raises(DomainError, match="HOST_LATCH_AUTHORITY_NOT_SEPARATED"):
        _host_latch(path, worker_owner).read()

    path.chmod(0o460)
    with pytest.raises(DomainError, match="HOST_LATCH_INSECURE_PERMISSIONS"):
        _host_latch(path, authority).read()

    supplementary_worker = HostProcessCredentials.unprivileged(
        authority.worker_uid,
        authority.worker_primary_gid + 1,
        frozenset({authority.read_gid}),
    )
    supplementary_authority = HostLatchAuthority(
        authority_uid=authority.authority_uid,
        read_gid=authority.read_gid,
        worker_uid=supplementary_worker.uid,
        worker_primary_gid=supplementary_worker.primary_gid,
        worker_supplementary_gids=supplementary_worker.supplementary_gids,
        trusted_root=authority.trusted_root,
    )
    with pytest.raises(DomainError, match="HOST_LATCH_INSECURE_PERMISSIONS"):
        _host_latch(path, supplementary_authority).read()


def test_host_latch_rejects_missing_ambiguous_or_mismatched_credentials(wp4) -> None:
    _, _, path, authority = wp4

    class MissingCredentials:
        def effective_credentials(self):
            raise OSError("credential source unavailable")

    with pytest.raises(DomainError, match="HOST_LATCH_CREDENTIALS_UNAVAILABLE"):
        HostLatch(path, authority, MissingCredentials()).read()
    ambiguous = HostProcessCredentials.unprivileged(-1, authority.read_gid, frozenset())
    with pytest.raises(DomainError, match="HOST_LATCH_CREDENTIALS_AMBIGUOUS"):
        HostLatch(path, authority, StaticHostCredentialProvider(ambiguous)).read()
    mismatched = HostProcessCredentials.unprivileged(
        authority.worker_uid + 1, authority.worker_primary_gid, frozenset()
    )
    with pytest.raises(DomainError, match="HOST_LATCH_WORKER_IDENTITY_MISMATCH"):
        HostLatch(path, authority, StaticHostCredentialProvider(mismatched)).read()


def test_host_latch_rejects_intermediate_symlink_and_parent_escape(tmp_path, wp4) -> None:
    _, _, path, authority = wp4
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o550)
    path.parent.chmod(0o750)
    intermediate = path.parent / "nested-link"
    intermediate.symlink_to(outside, target_is_directory=True)
    path.parent.chmod(0o550)
    with pytest.raises(DomainError, match="HOST_LATCH_INVALID_FILE_TYPE"):
        _host_latch(intermediate / "deeper" / "gate1.json", authority).read()
    with pytest.raises(ValueError, match="ambiguous component"):
        _host_latch(path.parent / "nested" / ".." / path.name, authority)


def test_host_latch_rejects_worker_writable_directory(wp4) -> None:
    _, service, path, _ = wp4
    path.parent.chmod(0o570)
    with pytest.raises(DomainError, match="HOST_LATCH_INSECURE_PERMISSIONS"):
        service.require_latch_clear()


def test_host_latch_rejects_non_regular_final_object(wp4) -> None:
    _, _, path, authority = wp4
    path.parent.chmod(0o750)
    directory_as_file = path.parent / "not-a-file"
    directory_as_file.mkdir(mode=0o550)
    path.parent.chmod(0o550)
    with pytest.raises(DomainError, match="HOST_LATCH_INVALID_FILE_TYPE"):
        _host_latch(directory_as_file, authority).read()


def test_host_latch_rejects_directory_replacement_during_walk(wp4, monkeypatch) -> None:
    import jarvis_pd.loop.application.host_latch as latch_module

    _, _, path, authority = wp4
    root = path.parent
    root.chmod(0o750)
    nested = root / "nested"
    nested.mkdir(mode=0o750)
    nested_path = nested / path.name
    nested_path.write_bytes(path.read_bytes())
    nested_path.chmod(0o440)
    nested.chmod(0o550)
    replacement = root / "replacement-directory"
    replacement.mkdir(mode=0o550)
    root.chmod(0o550)

    original_stat = latch_module.os.stat
    calls = 0

    def replacing_stat(component, *args, **kwargs):
        nonlocal calls
        if component == "nested" and kwargs.get("dir_fd") is not None:
            calls += 1
            if calls == 2:
                root.chmod(0o750)
                nested.rename(root / "original-directory")
                replacement.rename(nested)
                root.chmod(0o550)
        return original_stat(component, *args, **kwargs)

    monkeypatch.setattr(latch_module.os, "stat", replacing_stat)
    with pytest.raises(DomainError, match="HOST_LATCH_DIRECTORY_REPLACED"):
        _host_latch(nested_path, authority).read()


@pytest.mark.parametrize(
    "identity_change",
    [
        {"real_uid": 0},
        {"effective_uid": 0},
        {"saved_uid": 0},
        {"filesystem_uid": 0},
    ],
)
def test_host_latch_rejects_root_in_every_uid_slot(wp4, identity_change) -> None:
    _, _, path, authority = wp4
    observed = replace(authority.expected_worker, **identity_change)
    with pytest.raises(DomainError, match="HOST_LATCH_PRIVILEGED_UID"):
        HostLatch(path, authority, StaticHostCredentialProvider(observed)).read()


@pytest.mark.parametrize(
    "identity_change",
    [
        {"real_uid": 11001},
        {"effective_uid": 11002},
        {"saved_uid": 11003},
        {"filesystem_uid": 11004},
        {"real_gid": 12001},
        {"effective_gid": 12002},
        {"saved_gid": 12003},
        {"filesystem_gid": 12004},
    ],
)
def test_host_latch_rejects_divergent_uid_or_gid_slots(wp4, identity_change) -> None:
    _, _, path, authority = wp4
    observed = replace(authority.expected_worker, **identity_change)
    with pytest.raises(DomainError, match="HOST_LATCH_CREDENTIALS_DIVERGED"):
        HostLatch(path, authority, StaticHostCredentialProvider(observed)).read()


@pytest.mark.parametrize(
    "privilege_change",
    [
        {"capability_effective": 1},
        {"capability_permitted": 1},
        {"capability_inheritable": 1},
        {"capability_bounding": 1},
        {"capability_ambient": 1},
        {"no_new_privileges": False},
    ],
)
def test_host_latch_rejects_active_or_recoverable_linux_privileges(wp4, privilege_change) -> None:
    _, _, path, authority = wp4
    observed = replace(authority.expected_worker, **privilege_change)
    with pytest.raises(DomainError, match="HOST_LATCH_RECOVERABLE_PRIVILEGES"):
        HostLatch(path, authority, StaticHostCredentialProvider(observed)).read()


def test_linux_credential_provider_reads_complete_real_snapshot() -> None:
    observed = EffectiveHostCredentialProvider().effective_credentials()
    assert (
        observed.real_uid,
        observed.effective_uid,
        observed.saved_uid,
    ) == os.getresuid()
    assert (
        observed.real_gid,
        observed.effective_gid,
        observed.saved_gid,
    ) == os.getresgid()
    assert observed.supplementary_gids == frozenset(os.getgroups())
    assert isinstance(observed.filesystem_uid, int)
    assert isinstance(observed.filesystem_gid, int)


def test_linux_credential_provider_rejects_incomplete_or_malformed_proc_status(
    monkeypatch,
) -> None:
    provider = EffectiveHostCredentialProvider()
    uids = os.getresuid()
    gids = os.getresgid()
    complete = {
        "Uid": " ".join(str(value) for value in (*uids, uids[1])),
        "Gid": " ".join(str(value) for value in (*gids, gids[1])),
        "Groups": " ".join(str(value) for value in os.getgroups()),
        "CapInh": "0000000000000000",
        "CapPrm": "0000000000000000",
        "CapEff": "0000000000000000",
        "CapBnd": "0000000000000000",
        "CapAmb": "0000000000000000",
        "NoNewPrivs": "1",
    }
    monkeypatch.setattr(provider, "_proc_status", lambda: {**complete, "CapEff": "xyz"})
    with pytest.raises(ValueError, match="malformed capability"):
        provider.effective_credentials()
    monkeypatch.setattr(provider, "_proc_status", lambda: {"Uid": complete["Uid"]})
    with pytest.raises(KeyError):
        provider.effective_credentials()


def test_privileged_credentials_fail_before_any_canonical_work_changes(engine, wp4) -> None:
    clock, _, path, authority = wp4
    _, task_id = _ready_task(engine)
    privileged = replace(authority.expected_worker, capability_permitted=1)
    service = WorkerContinuityService(
        uow_factory(engine),
        clock,
        HostLatch(path, authority, StaticHostCredentialProvider(privileged)),
    )
    worker = LocalWorker(SelectionService(uow_factory(engine), clock), service)
    with pytest.raises(DomainError, match="HOST_LATCH_RECOVERABLE_PRIVILEGES"):
        worker.run_once(
            worker_id="worker",
            owner_id="owner",
            authorized_goal_id=uuid4(),
            execute=lambda _: None,
            request_id=uuid4(),
            idempotency_key="privileged-pre-acquire",
        )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM tasks WHERE id=:id"), {"id": task_id}
            ).scalar_one()
            == "READY"
        )
        assert connection.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM leases")).scalar_one() == 0


def test_credential_revalidation_before_effect_blocks_callback(engine, wp4) -> None:
    clock, _, path, authority = wp4
    goal_id, _ = _ready_task(engine)
    safe = authority.expected_worker
    unsafe = replace(safe, capability_ambient=1)

    class ChangingCredentials:
        def __init__(self) -> None:
            self.calls = 0

        def effective_credentials(self) -> HostProcessCredentials:
            self.calls += 1
            return safe if self.calls == 1 else unsafe

    credentials = ChangingCredentials()
    service = WorkerContinuityService(
        uow_factory(engine), clock, HostLatch(path, authority, credentials)
    )
    worker = LocalWorker(SelectionService(uow_factory(engine), clock), service)
    callback_calls = 0

    def execute(_: WorkerRun):
        nonlocal callback_calls
        callback_calls += 1
        return None

    with pytest.raises(DomainError, match="HOST_LATCH_RECOVERABLE_PRIVILEGES"):
        worker.run_once(
            worker_id="worker",
            owner_id="owner",
            authorized_goal_id=goal_id,
            execute=execute,
            request_id=uuid4(),
            idempotency_key="privilege-change-pre-effect",
        )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM runs")).scalar_one() == "PAUSED"
        assert connection.execute(text("SELECT state FROM tasks")).scalar_one() == "RUNNING"
    assert credentials.calls == 2 and callback_calls == 0


def test_host_latch_rejects_replacement_during_read(engine, wp4, monkeypatch) -> None:
    import jarvis_pd.loop.application.host_latch as latch_module

    _, service, path, _ = wp4
    replacement = path.parent / "replacement.json"
    path.parent.chmod(0o750)
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(0o440)
    path.parent.chmod(0o550)
    original_stat = latch_module.os.stat
    calls = 0

    def replacing_stat(*args, **kwargs):
        nonlocal calls
        if kwargs.get("dir_fd") is not None:
            calls += 1
            if calls == 2:
                os.chmod(path.parent, 0o750)
                os.replace(replacement, path)
                os.chmod(path.parent, 0o550)
        return original_stat(*args, **kwargs)

    monkeypatch.setattr(latch_module.os, "stat", replacing_stat)
    with pytest.raises(DomainError, match="HOST_LATCH_REPLACED"):
        service.require_latch_clear()


def test_latch_activation_after_acquisition_prevents_callback(engine, wp4) -> None:
    clock, service, path, _ = wp4
    goal_id, _ = _ready_task(engine)
    worker = LocalWorker(SelectionService(uow_factory(engine), clock), service)
    calls = 0

    def execute(_: WorkerRun):
        nonlocal calls
        calls += 1
        return None

    with pytest.raises(DomainError, match="HOST_LATCH_ACTIVE"):
        worker.run_once(
            worker_id="worker",
            owner_id="owner",
            authorized_goal_id=goal_id,
            execute=execute,
            request_id=uuid4(),
            idempotency_key="latch-race",
            after_acquisition=lambda: _set_latch(
                engine, path, clock, active=True, reason="human-stop", revision=2
            ),
        )
    with engine.connect() as connection:
        state = connection.execute(text("SELECT state FROM runs")).scalar_one()
    assert calls == 0 and state == "PAUSED"


@pytest.mark.parametrize("bad_result", [None, object(), "CONFIRMED_SUCCEEDED", 7])
def test_free_or_unexpected_callback_result_never_succeeds(engine, wp4, bad_result) -> None:
    clock, service, _, _ = wp4
    goal_id, _ = _ready_task(engine)
    worker = LocalWorker(SelectionService(uow_factory(engine), clock), service)
    with pytest.raises(DomainError, match="CANONICAL_RESULT_REQUIRED"):
        worker.run_once(
            worker_id="worker",
            owner_id="owner",
            authorized_goal_id=goal_id,
            execute=lambda _: bad_result,
            request_id=uuid4(),
            idempotency_key=f"bad-{type(bad_result)}",
        )
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT state FROM runs")).scalar_one()
            == "RECONCILIATION_REQUIRED"
        )


def test_real_wp3_effect_receipt_is_required_for_success(engine, wp4, tmp_path) -> None:
    clock, service, _, _ = wp4
    goal_id, _ = _ready_task(engine)
    worker = LocalWorker(SelectionService(uow_factory(engine), clock), service)
    captured: list[EffectReceipt] = []

    def execute(run: WorkerRun) -> EffectReceipt:
        receipt = _real_receipt(engine, clock, run, tmp_path / "canonical-success")
        captured.append(receipt)
        return receipt

    run = worker.run_once(
        worker_id="owner",
        owner_id="owner",
        authorized_goal_id=goal_id,
        execute=execute,
        request_id=uuid4(),
        idempotency_key="canonical-success",
    )
    assert captured[0].verified and captured[0].evidence_refs
    assert _states(engine, run) == ("SUCCEEDED", "COMPLETED", "RELEASED")


def test_missing_crossed_or_tampered_receipts_fail_closed(engine, wp4, tmp_path) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    receipt = _real_receipt(engine, clock, run, tmp_path / "canonical")
    missing = receipt.model_copy(update={"receipt_id": uuid4()})
    with pytest.raises(DomainError, match="CANONICAL_RECEIPT_MISSING"):
        service.apply_execution_result(run=run, result=missing, request_id=uuid4())
    assert _states(engine, run)[0] == "RUNNING"
    crossed_run = _running(engine, clock)
    crossed = _real_receipt(engine, clock, crossed_run, tmp_path / "crossed")
    with pytest.raises(DomainError, match="RECEIPT_RELATION_MISMATCH"):
        service.apply_execution_result(run=run, result=crossed, request_id=uuid4())
    tampered = receipt.model_copy(update={"tool_call_id": uuid4()})
    with pytest.raises(DomainError, match="RECEIPT_DOCUMENT_MISMATCH"):
        service.apply_execution_result(run=run, result=tampered, request_id=uuid4())


@pytest.mark.parametrize(
    "changes",
    [
        {"verified": False},
        {"resulting_hash": None},
        {"resulting_hash": "sha256:" + "0" * 64},
        {"evidence_refs": []},
        {"operation_id": uuid4()},
        {"outcome": "INVALID_ENUM"},
    ],
)
def test_unverified_hashless_evidenceless_or_wrong_operation_receipt_rejected(
    engine, wp4, tmp_path, changes
) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    receipt = _real_receipt(engine, clock, run, tmp_path / str(uuid4()))
    candidate = EffectReceipt.model_construct(**{**receipt.__dict__, **changes})
    with pytest.raises(DomainError):
        service.apply_execution_result(run=run, result=candidate, request_id=uuid4())
    assert _states(engine, run)[0] != "SUCCEEDED"


def test_stale_fencing_and_contradictory_worker_error_never_succeed(engine, wp4, tmp_path) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    receipt = _real_receipt(engine, clock, run, tmp_path / "stale")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE leases SET fencing_token=2 WHERE resource_id=:task"), {"task": run.task_id}
        )
    with pytest.raises(DomainError) as stale:
        service.apply_execution_result(run=run, result=receipt, request_id=uuid4())
    assert stale.value.code == ErrorCode.FENCING_REJECTED and _states(engine, run)[0] != "SUCCEEDED"


def test_contradictory_worker_error_with_success_receipt_requires_reconciliation(
    engine, wp4, tmp_path
) -> None:
    clock, service, _, _ = wp4
    goal_id, _ = _ready_task(engine)
    worker = LocalWorker(SelectionService(uow_factory(engine), clock), service)

    def execute(run: WorkerRun):
        receipt = _real_receipt(engine, clock, run, tmp_path / "contradictory")
        raise WorkerExecutionError("callback contradicted success", result=receipt)

    with pytest.raises(WorkerExecutionError):
        worker.run_once(
            worker_id="owner",
            owner_id="owner",
            authorized_goal_id=goal_id,
            execute=execute,
            request_id=uuid4(),
            idempotency_key="contradictory",
        )
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT state FROM runs")).scalar_one()
            == "RECONCILIATION_REQUIRED"
        )


def test_untyped_exception_requires_reconciliation_and_forbids_takeover(engine, wp4) -> None:
    clock, service, _, _ = wp4
    goal_id, _ = _ready_task(engine)
    worker = LocalWorker(SelectionService(uow_factory(engine), clock), service)
    with pytest.raises(RuntimeError, match="socket-lost"):
        worker.run_once(
            worker_id="owner",
            owner_id="owner",
            authorized_goal_id=goal_id,
            execute=lambda _: (_ for _ in ()).throw(RuntimeError("socket-lost")),
            request_id=uuid4(),
            idempotency_key="unknown",
        )
    clock.advance(timedelta(seconds=31))
    with engine.connect() as connection:
        run_id = connection.execute(text("SELECT id FROM runs")).scalar_one()
    with pytest.raises(DomainError):
        service.takeover(
            run_id=run_id, new_owner_id="replayer", ttl=timedelta(seconds=30), request_id=uuid4()
        )


def test_confirmed_failed_receipt_terminalizes_run_task_lease_atomically(
    engine, wp4, tmp_path
) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    receipt = _real_receipt(engine, clock, run, tmp_path / "failed", failed=True)
    assert receipt.outcome == ToolCallOutcome.FAILED
    assert (
        service.apply_execution_result(run=run, result=receipt, request_id=uuid4())
        == "CONFIRMED_FAILED"
    )
    assert _states(engine, run) == ("FAILED", "FAILED", "RELEASED")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM outbox WHERE aggregate_id=:id"), {"id": run.run_id}
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_records WHERE aggregate_id=:id"),
                {"id": run.run_id},
            ).scalar_one()
            == 1
        )


def test_nonrecoverable_terminalizes_run_task_and_lease(engine, wp4) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    service.record_interruption(
        run=run,
        phase="EXECUTE",
        classification="NON_RECOVERABLE",
        request_id=uuid4(),
        correlation_id=uuid4(),
        details={"reason": "deterministic"},
    )
    assert _states(engine, run) == ("FAILED", "FAILED", "RELEASED")


def test_terminal_failure_rolls_back_on_task_divergence_or_stale_lease(engine, wp4) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tasks SET state='BLOCKED' WHERE id=:id"), {"id": run.task_id}
        )
    with pytest.raises(DomainError):
        service.record_interruption(
            run=run,
            phase="EXECUTE",
            classification="NON_RECOVERABLE",
            request_id=uuid4(),
            correlation_id=uuid4(),
        )
    assert _states(engine, run) == ("RUNNING", "BLOCKED", "ACTIVE")

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tasks SET state='RUNNING' WHERE id=:id"), {"id": run.task_id}
        )
        connection.execute(
            text("UPDATE leases SET expires_at=:expired WHERE resource_id=:id"),
            {"id": run.task_id, "expired": clock.now() - timedelta(seconds=1)},
        )
    with pytest.raises(DomainError) as stale:
        service.record_interruption(
            run=run,
            phase="EXECUTE",
            classification="NON_RECOVERABLE",
            request_id=uuid4(),
            correlation_id=uuid4(),
        )
    assert stale.value.code == ErrorCode.FENCING_REJECTED
    assert _states(engine, run) == ("RUNNING", "RUNNING", "ACTIVE")


def test_confirmed_failure_rolls_back_run_task_lease_outbox_and_audit(
    engine, wp4, tmp_path
) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    receipt = _real_receipt(engine, clock, run, tmp_path / "rollback", failed=True)
    with engine.connect() as connection:
        before = (
            connection.execute(text("SELECT count(*) FROM outbox")).scalar_one(),
            connection.execute(text("SELECT count(*) FROM audit_records")).scalar_one(),
        )
    with pytest.raises(RuntimeError, match="terminal-injected"):
        service.apply_execution_result(
            run=run,
            result=receipt,
            request_id=uuid4(),
            before_outbox=lambda: (_ for _ in ()).throw(RuntimeError("terminal-injected")),
        )
    assert _states(engine, run) == ("RUNNING", "RUNNING", "ACTIVE")
    with engine.connect() as connection:
        after = (
            connection.execute(text("SELECT count(*) FROM outbox")).scalar_one(),
            connection.execute(text("SELECT count(*) FROM audit_records")).scalar_one(),
        )
    assert after == before


def test_terminal_failure_atomicity_and_concurrent_race(engine, wp4) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)

    def candidate():
        try:
            service.record_interruption(
                run=run,
                phase="EXECUTE",
                classification="NON_RECOVERABLE",
                request_id=uuid4(),
                correlation_id=uuid4(),
            )
            return "won"
        except DomainError:
            return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: candidate(), range(2)))
    assert results.count("won") == 1 and _states(engine, run) == ("FAILED", "FAILED", "RELEASED")


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-1)])
def test_nonpositive_ttl_rejected(engine, wp4, ttl) -> None:
    _, service, _, _ = wp4
    run = _running(engine, wp4[0], expired=True)
    operations = (
        lambda: service.heartbeat(
            worker_id="w", owner_id="o", status="IDLE", ttl=ttl, request_id=uuid4()
        ),
        lambda: service.renew_lease(
            resource_id=run.task_id,
            owner_id=run.owner_id,
            fencing_token=1,
            ttl=ttl,
            request_id=uuid4(),
        ),
        lambda: service.takeover(
            run_id=run.run_id, new_owner_id="owner-2", ttl=ttl, request_id=uuid4()
        ),
    )
    for operation in operations:
        with pytest.raises(DomainError, match="TTL_MUST_BE_POSITIVE"):
            operation()


def test_takeover_has_one_winner_and_monotonic_fencing(engine, wp4) -> None:
    clock, service, _, _ = wp4
    run = _running(engine, clock)
    clock.advance(timedelta(seconds=31))

    def candidate(owner: str):
        try:
            return service.takeover(
                run_id=run.run_id, new_owner_id=owner, ttl=timedelta(seconds=30), request_id=uuid4()
            )
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(candidate, ("owner-2", "owner-3")))
    winners = [result for result in results if isinstance(result, WorkerRun)]
    assert len(winners) == 1 and winners[0].fencing_token == 2


def test_inbox_identity_includes_type_payload_and_canonical_json(engine, wp4) -> None:
    _, service, _, _ = wp4
    message_id = uuid4()
    assert service.receive_once(
        message_id=message_id, message_type="internal.a", payload={"a": 1, "b": 2}
    )
    assert not service.receive_once(
        message_id=message_id, message_type="internal.a", payload={"b": 2, "a": 1}
    )
    for message_type, payload in (("internal.b", {"a": 1, "b": 2}), ("internal.a", {"a": 2})):
        with pytest.raises(DomainError) as conflict:
            service.receive_once(message_id=message_id, message_type=message_type, payload=payload)
        assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_dispatch_redelivery_after_confirmation_gap_materializes_once(
    engine, wp4, tmp_path
) -> None:
    clock, service, path, authority = wp4
    run = _running(engine, clock)
    receipt = _real_receipt(engine, clock, run, tmp_path / "outbox")
    service.apply_execution_result(run=run, result=receipt, request_id=uuid4())
    with engine.connect() as connection:
        outbox_id = connection.execute(
            text(
                "SELECT id FROM outbox WHERE aggregate_id=:id AND message_type='loop.run.completed'"
            ),
            {"id": run.run_id},
        ).scalar_one()
    effect_id, calls = uuid4(), 0

    def materialize(connection: Connection, payload: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        connection.execute(
            text(
                "INSERT INTO evidence(id,run_id,kind,payload) VALUES (:id,:run,'local-delivery',CAST(:payload AS jsonb))"
            ),
            {"id": effect_id, "run": run.run_id, "payload": json.dumps(payload)},
        )

    with pytest.raises(RuntimeError):
        service.dispatch_local(
            outbox_id=outbox_id,
            delivery_owner="d1",
            materialize=materialize,
            after_delivery_before_confirm=lambda: (_ for _ in ()).throw(RuntimeError("crash")),
        )
    restarted = WorkerContinuityService(uow_factory(engine), clock, _host_latch(path, authority))
    assert restarted.dispatch_local(
        outbox_id=outbox_id, delivery_owner="d2", materialize=materialize
    )
    assert calls == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM outbox WHERE id=:id"), {"id": outbox_id}
            ).scalar_one()
            == "RELEASED"
        )
