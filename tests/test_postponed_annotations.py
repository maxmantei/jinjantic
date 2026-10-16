from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from jinja2 import DictLoader, Environment
from pydantic import BaseModel

from jinjantic import TemplateRegistrationError, TemplateRegistry


class UserCard(BaseModel):
    name: str


class ErrorAlert(BaseModel):
    message: str


def make_registry() -> TemplateRegistry:
    registry = TemplateRegistry(
        Environment(
            loader=DictLoader(
                {
                    "user-card.html": "<h1>{{ name }}</h1>",
                    "error-alert.html": "<p>{{ message }}</p>",
                }
            )
        )
    )
    registry.register(UserCard, "user-card.html")
    registry.register(ErrorAlert, "error-alert.html")
    return registry


def test_postponed_return_annotation_still_resolves_model() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/card")
    @registry.endpoint
    async def get_card() -> UserCard:
        return UserCard(name="Ada")

    response = TestClient(app).get("/card")

    assert response.status_code == 200
    assert response.text == "<h1>Ada</h1>"


def test_postponed_union_annotation_resolves_models() -> None:
    registry = make_registry()
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


def test_postponed_annotations_still_detect_injected_response() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.post("/save")
    @registry.endpoint
    async def save(response: Response) -> UserCard:
        response.headers["HX-Trigger"] = "saved"
        response.status_code = 201
        return UserCard(name="Ada")

    response = TestClient(app).post("/save")

    assert response.status_code == 201
    assert response.headers["HX-Trigger"] == "saved"
    assert response.text == "<h1>Ada</h1>"


def test_locally_defined_model_with_postponed_annotation_raises_registration_error() -> (
    None
):
    registry = make_registry()

    class LocalCard(BaseModel):
        name: str

    async def get_card() -> LocalCard:
        return LocalCard(name="Ada")

    with pytest.raises(TemplateRegistrationError, match="explicit model"):
        registry.endpoint(get_card)
