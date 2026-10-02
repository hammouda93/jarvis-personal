from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .regression_registry import (
    RegressionRegistry,
    RegressionSpec,
    DEFAULT_REGRESSION_REGISTRY,
)


@dataclass(frozen=True)
class RegressionRunResult:
    test_id: str
    success: bool
    returncode: int
    duration_s: float
    stdout: str
    stderr: str
    error: str = ""


class RegressionRunner:
    """Execute only commands registered in the trusted RegressionRegistry."""

    def __init__(
        self,
        *,
        registry: RegressionRegistry | None = None,
        project_root: str | Path = ".",
        timeout_s: float = 300.0,
    ):
        self.registry = registry or DEFAULT_REGRESSION_REGISTRY
        self.project_root = Path(project_root)
        self.timeout_s = max(1.0, float(timeout_s))

    @staticmethod
    def _argv(spec: RegressionSpec) -> list[str]:
        command = str(spec.command or "").strip()
        if not command:
            raise ValueError("empty_regression_command")

        # Registry commands are trusted project metadata, but execution still
        # avoids shell=True. Current packs use either python or powershell.
        if command.startswith("python "):
            import shlex

            return shlex.split(command, posix=False)
        if command.lower().startswith("powershell "):
            import shlex

            return shlex.split(command, posix=False)
        raise ValueError("unsupported_regression_command")

    def run(self, test_id: str) -> RegressionRunResult:
        spec = self.registry.get(test_id)
        if spec is None:
            return RegressionRunResult(
                test_id=str(test_id),
                success=False,
                returncode=-1,
                duration_s=0.0,
                stdout="",
                stderr="",
                error="unknown_regression_test",
            )

        try:
            argv = self._argv(spec)
        except Exception as exc:
            return RegressionRunResult(
                test_id=spec.test_id,
                success=False,
                returncode=-1,
                duration_s=0.0,
                stdout="",
                stderr="",
                error=str(exc),
            )

        started = time.perf_counter()
        try:
            process = subprocess.run(
                argv,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            return RegressionRunResult(
                test_id=spec.test_id,
                success=False,
                returncode=-1,
                duration_s=time.perf_counter() - started,
                stdout=str(exc.stdout or "")[-12000:],
                stderr=str(exc.stderr or "")[-12000:],
                error="regression_timeout",
            )
        except Exception as exc:
            return RegressionRunResult(
                test_id=spec.test_id,
                success=False,
                returncode=-1,
                duration_s=time.perf_counter() - started,
                stdout="",
                stderr="",
                error=str(exc)[:1200],
            )

        return RegressionRunResult(
            test_id=spec.test_id,
            success=process.returncode == 0,
            returncode=int(process.returncode),
            duration_s=time.perf_counter() - started,
            stdout=(process.stdout or "")[-12000:],
            stderr=(process.stderr or "")[-12000:],
            error="" if process.returncode == 0 else "regression_failed",
        )

    def run_many(
        self,
        test_ids: list[str],
        *,
        stop_on_failure: bool = True,
    ) -> list[RegressionRunResult]:
        results: list[RegressionRunResult] = []
        for test_id in test_ids:
            result = self.run(test_id)
            results.append(result)
            if stop_on_failure and not result.success:
                break
        return results
