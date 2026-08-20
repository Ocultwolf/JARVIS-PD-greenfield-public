import hashlib
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4


class ConfirmedNoEffectError(RuntimeError):
    """Positive evidence that the filesystem commit point was not reached."""

    def __init__(self, code: str, previous_content: bytes | None = None) -> None:
        self.code = code
        self.previous_content = previous_content
        super().__init__(code)


class AmbiguousEffectError(RuntimeError):
    """The effect may have happened; only later observation can decide."""

    def __init__(
        self,
        code: str,
        previous_content: bytes | None,
        dispatched_content: bytes | None,
        *,
        phase: str,
    ) -> None:
        self.code = code
        self.previous_content = previous_content
        self.dispatched_content = dispatched_content
        self.phase = phase
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AtomicWriteEvidence:
    previous_content: bytes | None
    resulting_hash: str
    evidence_ref: str


class FixtureAtomicWrite:
    """Single local R1 fixture using directory FDs to contain symlinks and TOCTOU."""

    def __init__(self, root: Path, fault_hook: Callable[[str], None] | None = None) -> None:
        self.root = root
        self.fault_hook = fault_hook

    @staticmethod
    def digest(content: bytes) -> str:
        return "sha256:" + hashlib.sha256(content).hexdigest()

    @staticmethod
    def result_digest(content: bytes | None) -> str:
        """Hash a compensation result, representing absence without colliding with an empty file."""
        if content is None:
            return FixtureAtomicWrite.digest(b"fixture.atomic_write:ABSENT")
        return FixtureAtomicWrite.digest(content)

    def _fault(self, phase: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(phase)

    def _open_parent(self, relative: str) -> tuple[int, str]:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or any(p in {"", "."} for p in path.parts):
            raise ValueError("SANDBOX_PATH_DENIED")
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        current = root_fd
        try:
            for component in path.parts[:-1]:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                if current != root_fd:
                    os.close(current)
                current = next_fd
            if current == root_fd:
                root_fd = -1
            return current, path.name
        finally:
            if root_fd >= 0:
                os.close(root_fd)

    @staticmethod
    def _read_existing(parent_fd: int, name: str) -> bytes | None:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        try:
            return os.read(fd, 1_048_577)
        finally:
            os.close(fd)

    def execute(
        self, relative: str, content: bytes, *, max_bytes: int, ambiguous_after_write: bool = False
    ) -> AtomicWriteEvidence:
        if len(content) > max_bytes:
            raise ConfirmedNoEffectError("SANDBOX_SIZE_DENIED")
        try:
            self._fault("before_open_parent")
            parent_fd, name = self._open_parent(relative)
        except (OSError, ValueError) as error:
            raise ConfirmedNoEffectError("PRE_EFFECT_OPEN_FAILED") from error
        temp_name = f".jarvis-{uuid4().hex}.tmp"
        previous: bytes | None = None
        replaced = False
        try:
            try:
                previous = self._read_existing(parent_fd, name)
                self._fault("before_temp_create")
                temp_fd = os.open(
                    temp_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    os.write(temp_fd, content)
                    os.fsync(temp_fd)
                finally:
                    os.close(temp_fd)
                self._fault("before_replace")
            except (OSError, ValueError) as error:
                raise ConfirmedNoEffectError("PRE_EFFECT_PREPARE_FAILED", previous) from error
            try:
                # os.replace is the point of no return. An exception from this
                # dispatch, or anything after it, is never treated as known failure.
                os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                replaced = True
                self._fault("after_replace")
                os.fsync(parent_fd)
                self._fault("after_directory_fsync")
                observed = self._read_existing(parent_fd, name)
                self._fault("after_verification_read")
            except (OSError, ValueError) as error:
                phase = "POST_REPLACE" if replaced else "REPLACE_DISPATCH"
                raise AmbiguousEffectError(
                    f"{phase}_AMBIGUOUS", previous, content, phase=phase
                ) from error
            digest = self.digest(observed or b"")
            if observed != content:
                raise AmbiguousEffectError(
                    "POST_WRITE_VERIFICATION_AMBIGUOUS",
                    previous,
                    content,
                    phase="POST_REPLACE_VERIFICATION",
                )
            if ambiguous_after_write:
                raise AmbiguousEffectError(
                    "DISPATCH_RESPONSE_LOST", previous, content, phase="POST_REPLACE_RESPONSE"
                )
            return AtomicWriteEvidence(previous, digest, f"fixture:{relative}:{digest}")
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temp_name, dir_fd=parent_fd)
            os.close(parent_fd)

    def observe(self, relative: str) -> bytes | None:
        parent_fd, name = self._open_parent(relative)
        try:
            return self._read_existing(parent_fd, name)
        finally:
            os.close(parent_fd)

    def restore(
        self,
        relative: str,
        content: bytes | None,
        *,
        max_bytes: int,
        ambiguous_after_restore: bool = False,
    ) -> str:
        if content is not None:
            return self.execute(
                relative,
                content,
                max_bytes=max_bytes,
                ambiguous_after_write=ambiguous_after_restore,
            ).resulting_hash
        try:
            parent_fd, name = self._open_parent(relative)
        except (OSError, ValueError) as error:
            raise ConfirmedNoEffectError("COMPENSATION_PRE_EFFECT_OPEN_FAILED") from error
        unlinked = False
        try:
            try:
                self._fault("before_unlink")
                os.unlink(name, dir_fd=parent_fd)
                unlinked = True
                self._fault("after_unlink")
                os.fsync(parent_fd)
                self._fault("after_restore_directory_fsync")
                observed = self._read_existing(parent_fd, name)
            except FileNotFoundError:
                observed = None
            except (OSError, ValueError) as error:
                phase = "POST_UNLINK" if unlinked else "UNLINK_DISPATCH"
                raise AmbiguousEffectError(
                    f"COMPENSATION_{phase}_AMBIGUOUS", None, None, phase=phase
                ) from error
            if observed is not None:
                raise AmbiguousEffectError(
                    "COMPENSATION_ABSENCE_VERIFICATION_AMBIGUOUS",
                    None,
                    None,
                    phase="POST_UNLINK_VERIFICATION",
                )
            if ambiguous_after_restore:
                raise AmbiguousEffectError(
                    "COMPENSATION_RESPONSE_LOST", None, None, phase="POST_UNLINK_RESPONSE"
                )
            return self.result_digest(None)
        finally:
            os.close(parent_fd)
