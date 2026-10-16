from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from jinjantic import (
    TemplateRegistrationError,
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


def test_list_annotation_concatenates_rendered_fragments_in_order(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/rows")
    @registry.endpoint
    async def rows() -> list[UserRow]:
        return [UserRow(name="first"), UserRow(name="second")]

    response = TestClient(app).get("/rows")

    assert response.status_code == 200
    assert response.text == "<li>first</li><li>second</li>"


def test_tuple_annotation_concatenates_distinct_model_fragments(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/dashboard")
    @registry.endpoint
    async def dashboard() -> tuple[UserCard, Toast]:
        return (UserCard(name="Ada"), Toast(message="saved"))

    response = TestClient(app).get("/dashboard")

    assert response.text == "<h1>Ada</h1><span>saved</span>"


def test_sequence_of_union_elements_renders_each_runtime_model(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/feed")
    @registry.endpoint
    async def feed() -> Sequence[UserCard | ErrorAlert]:
        return [UserCard(name="Ada"), ErrorAlert(message="boom")]

    response = TestClient(app).get("/feed")

    assert response.text == "<h1>Ada</h1><p>boom</p>"


def test_sequence_with_unregistered_element_fails_at_decoration_time(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)

    async def rows() -> list[UnregisteredWidget]:
        return []

    with pytest.raises(UnregisteredModelError):
        registry.endpoint(rows)


def test_sequence_containing_non_model_fails_at_decoration_time(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)

    async def rows() -> list[str]:
        return []

    with pytest.raises(TemplateRegistrationError):
        registry.endpoint(rows)


def test_response_member_of_union_passes_through_untouched(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/go")
    @registry.endpoint
    async def go(redirect: bool = False) -> UserCard | RedirectResponse:
        if redirect:
            return RedirectResponse("/target", status_code=307)
        return UserCard(name="Ada")

    client = TestClient(app)

    rendered = client.get("/go")
    assert rendered.status_code == 200
    assert rendered.text == "<h1>Ada</h1>"

    passed_through = client.get("/go?redirect=true", follow_redirects=False)
    assert passed_through.status_code == 307
    assert passed_through.headers["location"] == "/target"
    assert passed_through.text == ""


def test_bare_response_annotation_passes_through(tmp_path: Path) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/legacy")
    @registry.endpoint
    async def legacy() -> RedirectResponse:
        return RedirectResponse("/target", status_code=307)

    response = TestClient(app).get("/legacy", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/target"


def test_injected_response_headers_cookie_and_status_are_merged(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.post("/save")
    @registry.endpoint
    async def save(response: Response) -> UserCard:
        response.headers["HX-Trigger"] = "saved"
        response.set_cookie("session", "abc")
        response.status_code = 201
        return UserCard(name="Ada")

    response = TestClient(app).post("/save")

    assert response.status_code == 201
    assert response.headers["HX-Trigger"] == "saved"
    assert response.cookies["session"] == "abc"
    assert response.text == "<h1>Ada</h1>"
    assert response.headers["content-type"] == "text/html; charset=utf-8"


def test_injected_response_without_status_keeps_endpoint_status(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/accepted")
    @registry.endpoint(status_code=202)
    async def accepted(response: Response) -> UserCard:
        response.headers["HX-Trigger"] = "accepted"
        return UserCard(name="Ada")

    response = TestClient(app).get("/accepted")

    assert response.status_code == 202
    assert response.headers["HX-Trigger"] == "accepted"


def test_injected_response_mutations_do_not_touch_pass_through(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/go")
    @registry.endpoint
    async def go(response: Response) -> UserCard | RedirectResponse:
        response.headers["HX-Trigger"] = "should-not-appear"
        response.set_cookie("session", "abc")
        response.status_code = 201
        return RedirectResponse("/target", status_code=307)

    response = TestClient(app).get("/go", follow_redirects=False)

    assert response.status_code == 307
    assert "HX-Trigger" not in response.headers
    assert "set-cookie" not in response.headers


def test_injected_response_merges_onto_sequence_rendering(
    tmp_path: Path,
) -> None:
    registry = build_registry(tmp_path)
    app = FastAPI()

    @app.get("/rows")
    @registry.endpoint
    def rows(response: Response) -> list[UserRow]:
        response.headers["HX-Trigger"] = "rows"
        return [UserRow(name="a"), UserRow(name="b")]

    response = TestClient(app).get("/rows")

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "rows"
    assert response.text == "<li>a</li><li>b</li>"
