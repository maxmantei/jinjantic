from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from jinjantic import (
    TemplateRegistry,
    UnregisteredModelError,
)

TEMPLATES = {
    "user-card.html": "<h1>{{ name }}</h1>",
    "error-alert.html": "<p>{{ message }}</p>",
    "toast.html": "<span>{{ message }}</span>",
    "user-row.html": "<li>{{ name }}</li>",
}


class UserCard(BaseModel):
    name: str


class ErrorAlert(BaseModel):
    message: str


class Toast(BaseModel):
    message: str


class UserRow(BaseModel):
    name: str


class UnregisteredWidget(BaseModel):
    name: str


def build_registry(tmp_path: Path) -> TemplateRegistry:
    for name, source in TEMPLATES.items():
        (tmp_path / name).write_text(source)
    registry = TemplateRegistry(tmp_path)
    registry.register(UserCard, "user-card.html")
    registry.register(ErrorAlert, "error-alert.html")
    registry.register(Toast, "toast.html")
    registry.register(UserRow, "user-row.html")
    return registry


def test_union_annotation_renders_returned_model(tmp_path: Path) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/alerts")
    @registry.endpoint
    async def alerts(kind: str = "user") -> UserCard | ErrorAlert:
        if kind == "error":
            return ErrorAlert(message="boom")
        return UserCard(name="Ada")

    client = TestClient(app)

    assert client.get("/alerts").text == "<h1>Ada</h1>"
    assert client.get("/alerts?kind=error").text == "<p>boom</p>"


def test_union_with_unregistered_member_fails_at_decoration_time(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)

    async def widget() -> UserCard | UnregisteredWidget:
        return UserCard(name="Ada")

    with pytest.raises(UnregisteredModelError):
        registry.endpoint(widget)
