from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _default_root() -> Path:
    root = Path(
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "workspaces"


@dataclass(frozen=True)
class WorkspaceArtifact:
    workspace_id: str
    relative_path: str
    size: int


class WorkspaceStorage:
    """Sandboxed local storage for future Dev Supervisor artifacts."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else _default_root()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_id(workspace_id: str) -> str:
        value = str(workspace_id or "").strip()
        if not value or any(
            token in value
            for token in ("/", "\\", "..")
        ):
            raise ValueError("invalid_workspace_id")
        return value

    def workspace_path(self, workspace_id: str) -> Path:
        value = self._validate_id(workspace_id)
        path = (self.root / value).resolve(strict=False)
        root = self.root.resolve(strict=False)
        path.relative_to(root)
        return path

    def _target(
        self,
        workspace_id: str,
        relative_path: str,
    ) -> Path:
        workspace = self.workspace_path(workspace_id)
        rel = Path(str(relative_path or ""))
        if rel.is_absolute():
            raise ValueError("absolute_path_not_allowed")
        target = (workspace / rel).resolve(strict=False)
        try:
            target.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("workspace_path_escape") from exc
        return target

    def ensure(self, workspace_id: str) -> Path:
        path = self.workspace_path(workspace_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_text(
        self,
        workspace_id: str,
        relative_path: str,
        content: str,
    ) -> WorkspaceArtifact:
        target = self._target(workspace_id, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return WorkspaceArtifact(
            workspace_id=self._validate_id(workspace_id),
            relative_path=str(
                target.relative_to(self.workspace_path(workspace_id))
            ).replace("\\", "/"),
            size=target.stat().st_size,
        )

    def read_text(
        self,
        workspace_id: str,
        relative_path: str,
        *,
        max_chars: int = 200000,
    ) -> str:
        target = self._target(workspace_id, relative_path)
        text = target.read_text(encoding="utf-8")
        if len(text) > max(1, int(max_chars)):
            raise ValueError("workspace_read_too_large")
        return text

    def list_files(
        self,
        workspace_id: str,
        *,
        limit: int = 500,
    ) -> list[WorkspaceArtifact]:
        workspace = self.ensure(workspace_id)
        result: list[WorkspaceArtifact] = []
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            result.append(
                WorkspaceArtifact(
                    workspace_id=self._validate_id(workspace_id),
                    relative_path=str(
                        path.relative_to(workspace)
                    ).replace("\\", "/"),
                    size=path.stat().st_size,
                )
            )
            if len(result) >= max(1, min(int(limit), 5000)):
                break
        return result

    def reset(self, workspace_id: str) -> None:
        workspace = self.workspace_path(workspace_id)
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
