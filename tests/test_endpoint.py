import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from jinja2 import DictLoader, Environment
from pydantic import BaseModel

from jinjantic import (
    TemplateRegistrationError,
    TemplateRegistry,
    UnregisteredModelError,
)


class UserCard(BaseModel):
    name: str


class ErrorAlert(BaseModel):
    message: str


class UnregisteredCard(BaseModel):
    name: str


def make_registry(template: str = "<h1>{{ name }}</h1>") -> TemplateRegistry:
    registry = TemplateRegistry(
        Environment(loader=DictLoader({"user-card.html": template}))
    )
    registry.register(UserCard, "user-card.html")
    return registry


def test_bare_decorator_on_async_route_renders_model() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/users/{user_id}")
    @registry.endpoint
    async def get_user(user_id: int) -> UserCard:
        return UserCard(name=f"user-{user_id}")

    response = TestClient(app).get("/users/7")

    assert response.status_code == 200
    assert response.text == "<h1>user-7</h1>"
    assert response.headers["content-type"] == "text/html; charset=utf-8"


def test_parameterized_decorator_honors_model_status_code_and_headers() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.post("/users")
    @registry.endpoint(UserCard, status_code=201, headers={"X-Custom": "1"})
    async def create_user() -> UserCard:
        return UserCard(name="Ada")

    response = TestClient(app).post("/users")

    assert response.status_code == 201
    assert response.headers["X-Custom"] == "1"
    assert response.text == "<h1>Ada</h1>"


def test_sync_def_route() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/card")
    @registry.endpoint
    def get_card() -> UserCard:
        return UserCard(name="Sync")

    response = TestClient(app).get("/card")

    assert response.status_code == 200
    assert response.text == "<h1>Sync</h1>"


def test_path_query_request_and_depends_are_resolved(tmp_path: Path) -> None:
    registry = make_registry()
    app = FastAPI()

    def pagination(offset: int = 0) -> int:
        return offset

    @app.get("/items/{item_id}")
    @registry.endpoint
    async def get_item(
        request: Request,
        item_id: int,
        q: str | None = None,
        offset: int = Depends(pagination),
    ) -> UserCard:
        return UserCard(name=f"{request.method} {item_id} {q} {offset}")

    response = TestClient(app).get("/items/3?q=go&offset=2")

    assert response.status_code == 200
    assert response.text == "<h1>GET 3 go 2</h1>"


def test_empty_call_decorator_infers_model_from_annotation() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/empty")
    @registry.endpoint()
    async def get_card() -> UserCard:
        return UserCard(name="Empty")

    response = TestClient(app).get("/empty")

    assert response.status_code == 200
    assert response.text == "<h1>Empty</h1>"


def test_keyword_options_without_model_infer_from_annotation() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/accepted")
    @registry.endpoint(status_code=202)
    async def get_card() -> UserCard:
        return UserCard(name="Accepted")

    response = TestClient(app).get("/accepted")

    assert response.status_code == 202
    assert response.text == "<h1>Accepted</h1>"


def test_custom_media_type_is_applied() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/plain")
    @registry.endpoint(UserCard, media_type="text/plain")
    async def get_card() -> UserCard:
        return UserCard(name="Plain")

    response = TestClient(app).get("/plain")

    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_openapi_builds_and_documents_html_response_not_model() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/users/{user_id}")
    @registry.endpoint
    async def get_user(user_id: int) -> UserCard:
        return UserCard(name=f"user-{user_id}")

    schema = app.openapi()

    assert schema["paths"]["/users/{user_id}"]["get"]["responses"]["200"]
    assert "UserCard" not in json.dumps(schema)


def test_missing_return_annotation_raises_at_decoration_time() -> None:
    registry = make_registry()

    async def get_card():
        return UserCard(name="Nope")

    with pytest.raises(TemplateRegistrationError):
        registry.endpoint(get_card)


def test_unregistered_model_annotation_raises_at_decoration_time() -> None:
    registry = make_registry()

    async def get_card() -> UnregisteredCard:
        return UnregisteredCard(name="Nope")

    with pytest.raises(UnregisteredModelError):
        registry.endpoint(get_card)


def test_unregistered_explicit_model_raises_at_decoration_time() -> None:
    registry = make_registry()

    async def get_card() -> UserCard:
        return UserCard(name="Nope")

    with pytest.raises(UnregisteredModelError):
        registry.endpoint(UnregisteredCard)(get_card)


def test_union_annotation_is_not_resolvable_yet() -> None:
    registry = make_registry()
    registry.register(ErrorAlert, "user-card.html")

    async def get_card() -> UserCard | ErrorAlert:
        return UserCard(name="Nope")

    with pytest.raises(TemplateRegistrationError):
        registry.endpoint(get_card)


def test_non_model_annotation_raises_at_decoration_time() -> None:
    registry = make_registry()

    async def get_count() -> int:
        return 1

    with pytest.raises(TemplateRegistrationError):
        registry.endpoint(get_count)


def test_response_result_passes_through_with_explicit_model() -> None:
    registry = make_registry()
    app = FastAPI()

    @app.get("/go")
    @registry.endpoint(model=UserCard)
    async def go() -> UserCard | RedirectResponse:
        return RedirectResponse("/target", status_code=303)

    response = TestClient(app).get("/go", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/target"
