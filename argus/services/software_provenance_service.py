from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import subprocess


_GIT_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
_SOURCE_HASH_SCHEMA = b"argus-source-tree@1\0"
_SOURCE_SCOPE = (
    "argus",
    "migrations",
    "main.py",
    "requirements.txt",
    "alembic.ini",
)
_IGNORED_SOURCE_PARTS = {"__pycache__"}
_IGNORED_SOURCE_SUFFIXES = {".pyc", ".pyo"}


@dataclass(frozen=True, slots=True)
class SoftwareProvenance:
    """Verified identifier for the exact Argus source being executed."""

    software_version: str
    kind: str
    revision: str


def resolve_software_provenance(
        source_root: Path | None = None,
) -> SoftwareProvenance:
    """Resolve clean Git provenance or hash an unpacked source tree."""

    root = (
        source_root.resolve()
        if source_root is not None
        else Path(__file__).resolve().parents[2]
    )
    if not root.is_dir():
        raise ValueError(
            f"Argus source root does not exist: {root}"
        )

    if (root / ".git").exists():
        return _resolve_git_provenance(root)
    return _resolve_source_tree_provenance(root)


def _resolve_git_provenance(root: Path) -> SoftwareProvenance:
    repository_root = Path(
        _run_git(root, "rev-parse", "--show-toplevel")
    ).resolve()
    if repository_root != root:
        raise ValueError(
            "Argus Git metadata belongs to a different source root: "
            f"expected {root}, found {repository_root}."
        )

    revision = _run_git(root, "rev-parse", "--verify", "HEAD")
    if _GIT_REVISION_PATTERN.fullmatch(revision) is None:
        raise ValueError(
            "Git returned an invalid Argus HEAD revision: "
            f"{revision!r}."
        )

    status = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        changes = status.splitlines()
        preview = "; ".join(changes[:10])
        if len(changes) > 10:
            preview += f"; ... ({len(changes) - 10} more)"
        raise ValueError(
            "Cannot prepare a reproducible analysis from a dirty "
            f"Argus Git worktree: {preview}. Commit or stash every "
            "change and retry."
        )

    return SoftwareProvenance(
        software_version=f"git:{revision}",
        kind="git",
        revision=revision,
    )


def _run_git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise ValueError(
            "Argus Git metadata is present, but Git could not be "
            "executed to verify software provenance."
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        suffix = f" Detail: {detail}" if detail else ""
        raise ValueError(
            "Git could not verify Argus software provenance."
            f"{suffix}"
        )
    return completed.stdout.strip()


def _resolve_source_tree_provenance(
        root: Path,
) -> SoftwareProvenance:
    files = _source_files(root)
    if not files:
        raise ValueError(
            "Cannot fingerprint unpacked Argus source: no source "
            "files were found."
        )

    digest = sha256()
    digest.update(_SOURCE_HASH_SCHEMA)
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")

    revision = digest.hexdigest()
    return SoftwareProvenance(
        software_version=f"source-sha256:{revision}",
        kind="source-sha256",
        revision=revision,
    )


def _source_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative_scope in _SOURCE_SCOPE:
        scoped_path = root / relative_scope
        if not scoped_path.exists():
            continue
        if scoped_path.is_symlink():
            raise ValueError(
                "Cannot fingerprint unpacked Argus source through "
                f"a symbolic link: {relative_scope}."
            )
        if scoped_path.is_file():
            files.append(scoped_path)
            continue
        for path in scoped_path.rglob("*"):
            if path.is_symlink():
                relative_path = path.relative_to(root).as_posix()
                raise ValueError(
                    "Cannot fingerprint unpacked Argus source "
                    f"through a symbolic link: {relative_path}."
                )
            if not path.is_file() or _is_ignored_source_file(path):
                continue
            files.append(path)
    return tuple(
        sorted(files, key=lambda path: path.relative_to(root).as_posix())
    )


def _is_ignored_source_file(path: Path) -> bool:
    return (
        any(part in _IGNORED_SOURCE_PARTS for part in path.parts)
        or path.suffix in _IGNORED_SOURCE_SUFFIXES
    )
