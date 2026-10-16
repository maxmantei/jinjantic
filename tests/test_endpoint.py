from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from jinja2 import DictLoader, Environment
from pydantic import BaseModel

from jinjantic import TemplateRegistry


class UserCard(BaseModel):
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
