import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jarvis_pd.shared.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class HostProcessCredentials:
    """Complete Linux worker identity relevant to privilege recovery."""

    real_uid: int
    effective_uid: int
    saved_uid: int
    filesystem_uid: int
    real_gid: int
    effective_gid: int
    saved_gid: int
    filesystem_gid: int
    supplementary_gids: frozenset[int]
    capability_inheritable: int
    capability_permitted: int
    capability_effective: int
    capability_bounding: int
    capability_ambient: int
    no_new_privileges: bool

    @classmethod
    def unprivileged(
        cls, uid: int, gid: int, supplementary_gids: frozenset[int]
    ) -> "HostProcessCredentials":
        return cls(uid, uid, uid, uid, gid, gid, gid, gid, supplementary_gids, 0, 0, 0, 0, 0, True)

    @property
    def uid(self) -> int:
        return self.effective_uid

    @property
    def primary_gid(self) -> int:
        return self.effective_gid


class HostCredentialProvider(Protocol):
    def effective_credentials(self) -> HostProcessCredentials: ...


class EffectiveHostCredentialProvider:
    """Reads and cross-checks complete credentials from Linux and procfs."""

    _STATUS_PATH = "/proc/self/status"
    _REQUIRED_FIELDS = frozenset(
        {
            "Uid",
            "Gid",
            "Groups",
            "CapInh",
            "CapPrm",
            "CapEff",
            "CapBnd",
            "CapAmb",
            "NoNewPrivs",
        }
    )

    @staticmethod
    def _decimal_quad(value: str) -> tuple[int, int, int, int]:
        parts = value.split()
        if len(parts) != 4 or any(re.fullmatch(r"[0-9]+", part) is None for part in parts):
            raise ValueError("malformed credential identity")
        parsed = tuple(int(part, 10) for part in parts)
        return parsed[0], parsed[1], parsed[2], parsed[3]

    @staticmethod
    def _capability(value: str) -> int:
        if re.fullmatch(r"[0-9a-fA-F]{16}", value) is None:
            raise ValueError("malformed capability set")
        return int(value, 16)

    def _proc_status(self) -> dict[str, str]:
        if sys.platform != "linux":
            raise OSError("complete latch credentials require Linux procfs")
        descriptor = os.open(self._STATUS_PATH, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            raw = os.read(descriptor, 65537)
        finally:
            os.close(descriptor)
        if not raw or len(raw) > 65536:
            raise ValueError("invalid proc status size")
        try:
            lines = raw.decode("ascii").splitlines()
        except UnicodeDecodeError as error:
            raise ValueError("non-ASCII proc status") from error
        values: dict[str, str] = {}
        for line in lines:
            name, separator, value = line.partition(":")
            if name not in self._REQUIRED_FIELDS:
                continue
            if not separator or name in values:
                raise ValueError("missing or duplicate proc credential field")
            values[name] = value.strip()
        if set(values) != self._REQUIRED_FIELDS:
            raise ValueError("incomplete proc credential fields")
        return values

    def effective_credentials(self) -> HostProcessCredentials:
        values = self._proc_status()
        uids = self._decimal_quad(values["Uid"])
        gids = self._decimal_quad(values["Gid"])
        group_parts = values["Groups"].split()
        if any(re.fullmatch(r"[0-9]+", group) is None for group in group_parts):
            raise ValueError("malformed supplementary groups")
        groups = frozenset(int(group, 10) for group in group_parts)
        if uids[:3] != os.getresuid() or gids[:3] != os.getresgid():
            raise ValueError("proc and kernel credential identities diverged")
        if (
            uids[1] != os.geteuid()
            or gids[1] != os.getegid()
            or groups != frozenset(os.getgroups())
        ):
            raise ValueError("proc and effective credentials diverged")
        if values["NoNewPrivs"] not in {"0", "1"}:
            raise ValueError("malformed NoNewPrivs")
        return HostProcessCredentials(
            *uids,
            *gids,
            groups,
            self._capability(values["CapInh"]),
            self._capability(values["CapPrm"]),
            self._capability(values["CapEff"]),
            self._capability(values["CapBnd"]),
            self._capability(values["CapAmb"]),
            values["NoNewPrivs"] == "1",
        )


@dataclass(frozen=True, slots=True)
class StaticHostCredentialProvider:
    """Acceptance-only credential boundary; filesystem metadata remains real."""

    credentials: HostProcessCredentials

    def effective_credentials(self) -> HostProcessCredentials:
        return self.credentials


@dataclass(frozen=True, slots=True)
class HostLatchAuthority:
    """HumanOwner-controlled identity and the worker identity expected at runtime."""

    authority_uid: int
    read_gid: int
    worker_uid: int
    worker_primary_gid: int
    worker_supplementary_gids: frozenset[int]
    trusted_root: Path

    @property
    def expected_worker(self) -> HostProcessCredentials:
        return HostProcessCredentials.unprivileged(
            self.worker_uid,
            self.worker_primary_gid,
            self.worker_supplementary_gids,
        )


@dataclass(frozen=True, slots=True)
class HostLatchState:
    active: bool
    reason: str
    revision: int
    state_hash: str


class HostLatch:
    """Read-only host latch resolved beneath one explicit trusted-root descriptor.

    There is deliberately no create, write, chmod, deactivate, or release operation.
    """

    def __init__(
        self,
        path: Path,
        authority: HostLatchAuthority,
        credentials: HostCredentialProvider | None = None,
    ) -> None:
        if not path.is_absolute() or not authority.trusted_root.is_absolute():
            raise ValueError("Host latch path and trusted root must be absolute")
        try:
            relative = path.relative_to(authority.trusted_root)
        except ValueError as error:
            raise ValueError("Host latch path must remain beneath trusted root") from error
        raw_parts = str(relative).split(os.sep)
        if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError("Host latch path contains an ambiguous component")
        self.path = path
        self.authority = authority
        self._parts = tuple(raw_parts)
        self._credentials = credentials or EffectiveHostCredentialProvider()

    def _worker_credentials(self) -> HostProcessCredentials:
        try:
            observed = self._credentials.effective_credentials()
        except Exception as error:
            raise DomainError(
                ErrorCode.UNAUTHORIZED, "HOST_LATCH_CREDENTIALS_UNAVAILABLE"
            ) from error
        expected = self.authority.expected_worker
        identity_values = (
            self.authority.authority_uid,
            self.authority.read_gid,
            observed.real_uid,
            observed.effective_uid,
            observed.saved_uid,
            observed.filesystem_uid,
            observed.real_gid,
            observed.effective_gid,
            observed.saved_gid,
            observed.filesystem_gid,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in identity_values
        ):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_CREDENTIALS_AMBIGUOUS")
        if not isinstance(observed.supplementary_gids, frozenset) or not isinstance(
            expected.supplementary_gids, frozenset
        ):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_CREDENTIALS_AMBIGUOUS")
        if any(
            not isinstance(group, int) or isinstance(group, bool) or group < 0
            for group in observed.supplementary_gids | expected.supplementary_gids
        ):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_CREDENTIALS_AMBIGUOUS")
        if 0 in {
            observed.real_uid,
            observed.effective_uid,
            observed.saved_uid,
            observed.filesystem_uid,
        }:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_PRIVILEGED_UID")
        if (
            len(
                {
                    observed.real_uid,
                    observed.effective_uid,
                    observed.saved_uid,
                    observed.filesystem_uid,
                }
            )
            != 1
            or len(
                {
                    observed.real_gid,
                    observed.effective_gid,
                    observed.saved_gid,
                    observed.filesystem_gid,
                }
            )
            != 1
        ):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_CREDENTIALS_DIVERGED")
        capability_sets = (
            observed.capability_inheritable,
            observed.capability_permitted,
            observed.capability_effective,
            observed.capability_bounding,
            observed.capability_ambient,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in capability_sets
        ) or not isinstance(observed.no_new_privileges, bool):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_CREDENTIALS_AMBIGUOUS")
        if any(capability_sets) or not observed.no_new_privileges:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_RECOVERABLE_PRIVILEGES")
        if observed != expected:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_WORKER_IDENTITY_MISMATCH")
        if observed.uid == self.authority.authority_uid:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_AUTHORITY_NOT_SEPARATED")
        worker_groups = observed.supplementary_gids | {observed.primary_gid}
        if self.authority.read_gid not in worker_groups:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_READ_GROUP_MISSING")
        return observed

    def _validate_identity(
        self,
        metadata: os.stat_result,
        *,
        directory: bool,
        worker: HostProcessCredentials,
        root_device: int,
    ) -> None:
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_type(metadata.st_mode):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_INVALID_FILE_TYPE")
        if metadata.st_dev != root_device:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_UNEXPECTED_MOUNT")
        if (
            metadata.st_uid != self.authority.authority_uid
            or metadata.st_gid != self.authority.read_gid
        ):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_AUTHORITY_MISMATCH")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_INSECURE_PERMISSIONS")
        worker_groups = worker.supplementary_gids | {worker.primary_gid}
        worker_can_write = bool(
            (worker.uid == metadata.st_uid and metadata.st_mode & stat.S_IWUSR)
            or (metadata.st_gid in worker_groups and metadata.st_mode & stat.S_IWGRP)
            or metadata.st_mode & stat.S_IWOTH
        )
        if worker.uid == metadata.st_uid or worker_can_write:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_WORKER_CAN_WRITE")
        if metadata.st_gid not in worker_groups or not metadata.st_mode & stat.S_IRGRP:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_WORKER_CANNOT_READ")
        if directory and not metadata.st_mode & stat.S_IXGRP:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_WORKER_CANNOT_TRAVERSE")

    @staticmethod
    def _same_object(left: os.stat_result, right: os.stat_result) -> bool:
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    def _open_root(self, worker: HostProcessCredentials) -> tuple[int, os.stat_result]:
        try:
            before = os.lstat(self.authority.trusted_root)
            descriptor = os.open(
                self.authority.trusted_root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            opened = os.fstat(descriptor)
            self._validate_identity(
                opened, directory=True, worker=worker, root_device=opened.st_dev
            )
            if not self._same_object(before, opened):
                raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_ROOT_REPLACED")
            return descriptor, opened
        except (OSError, DomainError) as error:
            if "descriptor" in locals():
                os.close(descriptor)
            if isinstance(error, DomainError):
                raise
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_ROOT_UNTRUSTED") from error

    def read(self) -> HostLatchState:
        worker = self._worker_credentials()
        root_descriptor, root_metadata = self._open_root(worker)
        descriptors = [root_descriptor]
        parent_descriptor = root_descriptor
        try:
            for component in self._parts[:-1]:
                descriptor: int | None = None
                try:
                    before = os.stat(component, dir_fd=parent_descriptor, follow_symlinks=False)
                    self._validate_identity(
                        before,
                        directory=True,
                        worker=worker,
                        root_device=root_metadata.st_dev,
                    )
                    descriptor = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent_descriptor,
                    )
                    opened = os.fstat(descriptor)
                    self._validate_identity(
                        opened,
                        directory=True,
                        worker=worker,
                        root_device=root_metadata.st_dev,
                    )
                    after = os.stat(component, dir_fd=parent_descriptor, follow_symlinks=False)
                    if not self._same_object(before, opened) or not self._same_object(
                        opened, after
                    ):
                        raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_DIRECTORY_REPLACED")
                except (OSError, DomainError) as error:
                    if descriptor is not None and descriptor not in descriptors:
                        os.close(descriptor)
                    if isinstance(error, DomainError):
                        raise
                    raise DomainError(
                        ErrorCode.UNAUTHORIZED, "HOST_LATCH_PATH_UNTRUSTED"
                    ) from error
                assert descriptor is not None
                descriptors.append(descriptor)
                parent_descriptor = descriptor

            filename = self._parts[-1]
            try:
                before = os.stat(filename, dir_fd=parent_descriptor, follow_symlinks=False)
                self._validate_identity(
                    before,
                    directory=False,
                    worker=worker,
                    root_device=root_metadata.st_dev,
                )
                file_descriptor = os.open(
                    filename,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=parent_descriptor,
                )
            except (OSError, DomainError) as error:
                if isinstance(error, DomainError):
                    raise
                raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_UNREADABLE") from error
            try:
                metadata = os.fstat(file_descriptor)
                self._validate_identity(
                    metadata,
                    directory=False,
                    worker=worker,
                    root_device=root_metadata.st_dev,
                )
                if not self._same_object(before, metadata):
                    raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_REPLACED")
                if metadata.st_size > 4096:
                    raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_INVALID_SIZE")
                raw = os.read(file_descriptor, 4097)
                after = os.stat(filename, dir_fd=parent_descriptor, follow_symlinks=False)
                if not self._same_object(metadata, after):
                    raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_REPLACED")
            finally:
                os.close(file_descriptor)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        if not raw or len(raw) > 4096:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_INVALID_SIZE")
        try:
            document: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_INVALID_JSON") from error
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "latch_id",
            "active",
            "reason",
            "revision",
        }:
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_INVALID_SCHEMA")
        if (
            document["schema_version"] != "1.0.0"
            or document["latch_id"] != "gate1-local"
            or not isinstance(document["active"], bool)
            or not isinstance(document["reason"], str)
            or not document["reason"]
            or not isinstance(document["revision"], int)
            or isinstance(document["revision"], bool)
            or document["revision"] < 1
        ):
            raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_INVALID_SCHEMA")
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        return HostLatchState(
            active=document["active"],
            reason=document["reason"],
            revision=document["revision"],
            state_hash=digest,
        )
