"""In-app inbox for fired alerts (M12) and the alert engine's start-up.

    GET  /me/inbox            fired alerts, newest first, with the unread count (the bell)
    POST /me/inbox/read       mark some (ids) or all as read

The alert engine itself is regimecast/alerts/engine.py; it runs in the API process.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field

from regimecast.alerts.engine import engine as alert_engine
from regimecast.api.accounts import NOT_READY, Ok, _iso, current_user, get_conn
from regimecast.db.schema import alert_events


class InboxItem(BaseModel):
    id: int
    alert_id: int | None = Field(description="The alert that fired (null if it was deleted since)")
    symbol: str
    kind: str
    title: str
    message: str
    value: float | None
    detail: dict[str, Any] | None
    fired_at: str
    read: bool


class InboxOut(BaseModel):
    items: list[InboxItem] = Field(description="Newest first")
    unread: int


class ReadIn(BaseModel):
    ids: list[int] | None = Field(None, max_length=500, description="Leave out to mark everything as read")


class EngineStatus(BaseModel):
    running: bool
    last_run: str | None
    last_fired: int
    every_seconds: float


router = APIRouter(tags=["alerts"])


@router.get("/me/inbox", response_model=InboxOut, responses={401: {}, **NOT_READY})
def inbox(limit: int = Query(30, ge=1, le=200), unread_only: bool = False,
          uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> InboxOut:
    where = [alert_events.c.user_id == uid] + ([alert_events.c.read_at.is_(None)] if unread_only else [])
    rows = conn.execute(sa.select(alert_events).where(*where)
                        .order_by(alert_events.c.fired_at.desc(), alert_events.c.id.desc()).limit(limit)).mappings()
    unread = conn.execute(sa.select(sa.func.count()).select_from(alert_events)
                          .where(alert_events.c.user_id == uid, alert_events.c.read_at.is_(None))).scalar_one()
    return InboxOut(items=[InboxItem(id=r["id"], alert_id=r["alert_id"], symbol=r["symbol"], kind=r["kind"],
                                     title=r["title"], message=r["message"], value=r["value"], detail=r["detail"],
                                     fired_at=_iso(r["fired_at"]), read=r["read_at"] is not None) for r in rows],
                    unread=unread)


@router.post("/me/inbox/read", response_model=Ok, responses={401: {}, **NOT_READY})
def mark_read(body: ReadIn, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> Ok:
    q = sa.update(alert_events).where(alert_events.c.user_id == uid, alert_events.c.read_at.is_(None))
    if body.ids is not None:
        q = q.where(alert_events.c.id.in_(body.ids))
    conn.execute(q.values(read_at=datetime.now(timezone.utc)))
    return Ok()


@router.get("/alerts/engine", response_model=EngineStatus)
def engine_status() -> EngineStatus:
    """Whether the alert engine is running in this API process, and its last run."""
    return EngineStatus(running=alert_engine._thread is not None, last_run=alert_engine.last_run,
                        last_fired=alert_engine.last_fired, every_seconds=alert_engine.every)


def install(app: FastAPI) -> None:
    app.include_router(router)
