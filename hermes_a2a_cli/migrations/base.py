from __future__ import annotations

from pathlib import Path
from typing import Protocol


class MigrationStep(Protocol):
    id: str
    from_version: str
    to_version: str

    def precheck(self, home: Path) -> None: ...

    def apply(self, home: Path, backup_id: str) -> None: ...

    def verify(self, home: Path) -> None: ...

    def rollback(self, home: Path, backup_id: str) -> None: ...
