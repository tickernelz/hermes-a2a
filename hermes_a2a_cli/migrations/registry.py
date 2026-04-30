from __future__ import annotations

from collections.abc import Sequence

from .base import MigrationStep

MIGRATIONS: list[MigrationStep] = []


class MigrationPlanError(ValueError):
    pass


def register_migration(migration: MigrationStep, migrations: list[MigrationStep] | None = None) -> list[MigrationStep]:
    target = migrations if migrations is not None else MIGRATIONS
    edge = (migration.from_version, migration.to_version)
    if any((step.from_version, step.to_version) == edge for step in target):
        raise MigrationPlanError(f"Duplicate migration edge: {migration.from_version} -> {migration.to_version}")
    target.append(migration)
    return target


def build_migration_plan(from_version: str, to_version: str, *, migrations: Sequence[MigrationStep] | None = None) -> list[MigrationStep]:
    if from_version == to_version:
        return []
    available = list(MIGRATIONS if migrations is None else migrations)
    plan: list[MigrationStep] = []
    current = from_version
    seen = {current}
    while current != to_version:
        step = next((candidate for candidate in available if candidate.from_version == current), None)
        if step is None:
            raise MigrationPlanError(f"No migration path from {current} to {to_version}")
        plan.append(step)
        current = step.to_version
        if current in seen:
            raise MigrationPlanError(f"Migration cycle detected at {current}")
        seen.add(current)
    return plan
