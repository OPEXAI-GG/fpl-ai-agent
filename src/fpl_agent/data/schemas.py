"""Pydantic models for the parts of FPL responses the agent relies on.

Extra fields are kept (extra="allow") so the feature store never drops data.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class Event(_Base):
    id: int
    name: str
    deadline_time: datetime
    finished: bool
    is_current: bool
    is_next: bool


class Team(_Base):
    id: int
    name: str
    short_name: str
    code: int


class Element(_Base):
    id: int
    web_name: str
    team: int
    element_type: int
    now_cost: int
    status: str
    chance_of_playing_next_round: int | None = None
    news: str = ""
    total_points: int
    minutes: int


class Bootstrap(_Base):
    events: list[Event]
    teams: list[Team]
    elements: list[Element]
    element_types: list[dict]


class Fixture(_Base):
    id: int
    event: int | None = None
    team_h: int
    team_a: int
    kickoff_time: datetime | None = None
    finished: bool
    team_h_score: int | None = None
    team_a_score: int | None = None
