"""Fail-closed executable entrypoint for the dedicated ARQ Worker image."""

from __future__ import annotations

import os
import re
import shutil

from app.core.config import get_settings


def validate_worker_runtime() -> None:
    settings = get_settings()
    errors = list(settings.runtime_coordinate_errors)
    if settings.runtime_environment == "development":
        errors.append("RUNTIME_ENVIRONMENT must be preview or production")
    if settings.generation_execution_mode != "arq":
        errors.append("TASK_EXECUTION_MODE must select arq")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", settings.worker_image_digest.strip().lower()):
        errors.append("WORKER_IMAGE_DIGEST must be a sha256 OCI digest")
    if errors:
        raise RuntimeError("worker_runtime_invalid: " + "; ".join(sorted(set(errors))))


def main() -> None:
    validate_worker_runtime()
    executable = shutil.which("arq")
    if not executable:
        raise RuntimeError("arq_executable_missing")
    os.execv(executable, [executable, "app.worker.WorkerSettings"])


if __name__ == "__main__":
    main()
