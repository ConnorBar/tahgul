"""Rate resolution (SPEC: core concepts, "Rate resolution").

Most specific wins:

    time-entry override → activity → scope → project → client → workspace default

A rate of 0 is a real override (pro bono work), so only ``None`` falls through.
The resolved rate is frozen onto each time entry when it is saved (milestone 3),
so changing a rate later only affects new work.
"""

from dataclasses import dataclass
from decimal import Decimal

from tahgul.models import Activity, Project

# Most specific first.
LEVELS = ("entry", "activity", "scope", "project", "client", "workspace")


@dataclass(frozen=True)
class ResolvedRate:
    rate: Decimal | None
    # Which level supplied the rate, or None when no level sets one.
    source: str | None


def resolve_rate(
    *,
    workspace: Decimal | None = None,
    client: Decimal | None = None,
    project: Decimal | None = None,
    scope: Decimal | None = None,
    activity: Decimal | None = None,
    entry: Decimal | None = None,
) -> ResolvedRate:
    values = {
        "entry": entry,
        "activity": activity,
        "scope": scope,
        "project": project,
        "client": client,
        "workspace": workspace,
    }
    for level in LEVELS:
        if values[level] is not None:
            return ResolvedRate(Decimal(values[level]), level)
    return ResolvedRate(None, None)


def project_rate(
    project: Project,
    activity: Activity | None = None,
    entry: Decimal | None = None,
) -> ResolvedRate:
    """Effective rate for work on a project, optionally for one activity.

    Scopes arrive in milestone 7 and slot in between activity and project.
    """
    return resolve_rate(
        workspace=project.workspace.default_rate,
        client=project.client.rate if project.client and not project.client.deleted_at else None,
        project=project.rate,
        activity=activity.rate if activity else None,
        entry=entry,
    )
