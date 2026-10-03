from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    task_root: Path
    schema_version: str = "1.0"
    log_level: str = "INFO"

    @classmethod
    def from_task_root(cls, task_root: str | Path) -> "Config":
        root = Path(task_root).expanduser().resolve()
        return cls(root, log_level=os.getenv("SURFACE_INTEL_LOG_LEVEL", "INFO"))

    @property
    def database_path(self) -> Path:
        return self.task_root / "task.sqlite"

    def write(self) -> None:
        self.task_root.mkdir(parents=True, exist_ok=True)
        (self.task_root / "config.json").write_text(
            json.dumps(
                {"schema_version": self.schema_version, "log_level": self.log_level},
                indent=2,
            ),
            encoding="utf-8",
        )
