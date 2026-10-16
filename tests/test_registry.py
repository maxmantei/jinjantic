from pathlib import Path
from typing import Any

import pytest
from conftest import bump_mtime, write_template
from fastapi.responses import HTMLResponse
from jinja2 import (
    DictLoader,
    Environment,
    FileSystemLoader,
    TemplateNotFound,
    select_autoescape,
)
from pydantic import BaseModel, ConfigDict, computed_field

from jinjantic import (
    TemplateRegistrationError,
    TemplateRegistry,
    UnregisteredModelError,
)


class Card(BaseModel):
    name: str


def test_registry_from_directory_path(tmp_path: Path) -> None:
    write_template(tmp_path, "card.html", "Hello {{ name }}!")

    registry = TemplateRegistry(tmp_path)
    registry.register(Card, "card.html")

    assert registry.render(Card(name="Ada")) == "Hello Ada!"


def test_registry_from_sequence_of_paths(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_template(second, "card.html", "Hello {{ name }}!")

    registry = TemplateRegistry([first, second])
    registry.register(Card, "card.html")

    assert registry.render(Card(name="Ada")) == "Hello Ada!"


def test_registry_from_existing_environment() -> None:
    env = Environment(loader=DictLoader({"card.html": "Hi {{ name }}"}))
    registry = TemplateRegistry(env)
    registry.register(Card, "card.html")

    assert registry.render(Card(name="Ada")) == "Hi Ada"


def test_register_returns_the_model_class() -> None:
    env = Environment(loader=DictLoader({"card.html": "{{ name }}"}))
    registry = TemplateRegistry(env)

    assert registry.register(Card, "card.html") is Card


def test_template_decorator_registers_model() -> None:
    env = Environment(loader=DictLoader({"user.html": "User {{ name }}"}))
    registry = TemplateRegistry(env)

    @registry.template("user.html")
    class UserCard(BaseModel):
        name: str

    assert issubclass(UserCard, BaseModel)
    assert registry.render(UserCard(name="Ada")) == "User Ada"


def test_registry_from_string_path(tmp_path: Path) -> None:
    write_template(tmp_path, "card.html", "Hello {{ name }}!")

    registry = TemplateRegistry(str(tmp_path))
    registry.register(Card, "card.html")

    assert registry.render(Card(name="Ada")) == "Hello Ada!"


def test_unregistered_model_cannot_be_rendered() -> None:
    env = Environment(loader=DictLoader({"card.html": "{{ name }}"}))
    registry = TemplateRegistry(env)

    with pytest.raises(UnregisteredModelError):
        registry.render(Card(name="Ada"))


def test_duplicate_registration_raises() -> None:
    env = Environment(loader=DictLoader({"a.html": "a", "b.html": "b"}))
    registry = TemplateRegistry(env)
    registry.register(Card, "a.html")

    with pytest.raises(TemplateRegistrationError):
        registry.register(Card, "b.html")


def test_duplicate_registration_via_decorator_raises() -> None:
    env = Environment(loader=DictLoader({"a.html": "a"}))
    registry = TemplateRegistry(env)

    class DuplicateCard(BaseModel):
        name: str

    registry.register(DuplicateCard, "a.html")

    with pytest.raises(TemplateRegistrationError):
        registry.template("a.html")(DuplicateCard)


def test_multiple_models_may_share_one_template() -> None:
    env = Environment(loader=DictLoader({"row.html": "{{ name }}"}))
    registry = TemplateRegistry(env)

    class UserRow(BaseModel):
        name: str

    class ProductRow(BaseModel):
        name: str

    registry.register(UserRow, "row.html")
    registry.register(ProductRow, "row.html")

    assert registry.render(UserRow(name="Ada")) == "Ada"
    assert registry.render(ProductRow(name="Widget")) == "Widget"


def test_missing_template_raises_on_eager_validation(tmp_path: Path) -> None:
    registry = TemplateRegistry(tmp_path)

    with pytest.raises(TemplateNotFound):
        registry.register(Card, "missing.html")


def test_deferred_validation_reports_missing_template(tmp_path: Path) -> None:
    registry = TemplateRegistry(tmp_path, validate_on_register=False)
    registry.register(Card, "missing.html")

    with pytest.raises(TemplateNotFound):
        registry.validate()


def test_deferred_validation_passes_once_template_exists(tmp_path: Path) -> None:
    registry = TemplateRegistry(tmp_path, validate_on_register=False)
    registry.register(Card, "card.html")
    write_template(tmp_path, "card.html", "Hello {{ name }}!")

    registry.validate()

    assert registry.render(Card(name="Ada")) == "Hello Ada!"


def test_render_resolves_template_lazily_when_not_eagerly_validated(
    tmp_path: Path,
) -> None:
    registry = TemplateRegistry(tmp_path, validate_on_register=False)
    registry.register(Card, "card.html")
    write_template(tmp_path, "card.html", "Late {{ name }}")

    assert registry.render(Card(name="Ada")) == "Late Ada"


def test_deferred_lazy_resolve_caches_template_on_binding(tmp_path: Path) -> None:
    path = write_template(tmp_path, "card.html", "Hello {{ name }}!")
    registry = TemplateRegistry(tmp_path, validate_on_register=False)
    registry.register(Card, "card.html")

    assert registry.render(Card(name="Ada")) == "Hello Ada!"

    path.unlink()

    assert registry.render(Card(name="Ada")) == "Hello Ada!"


def test_deferred_lazy_resolve_does_no_disk_io_with_uncached_environment(
    tmp_path: Path,
) -> None:
    path = write_template(tmp_path, "card.html", "Hello {{ name }}!")
    env = Environment(
        loader=FileSystemLoader(tmp_path), cache_size=0, auto_reload=False
    )
    registry = TemplateRegistry(env, validate_on_register=False)
    registry.register(Card, "card.html")

    assert registry.render(Card(name="Ada")) == "Hello Ada!"

    path.unlink()

    assert registry.render(Card(name="Ada")) == "Hello Ada!"


def test_context_unpacks_fields_computed_fields_and_extras() -> None:
    class User(BaseModel):
        model_config = ConfigDict(extra="allow")

        first: str
        last: str

        @computed_field
        @property
        def full_name(self) -> str:
            return f"{self.first} {self.last}"

    env = Environment(
        loader=DictLoader({"user.html": "{{ first }}|{{ full_name }}|{{ nickname }}"})
    )
    registry = TemplateRegistry(env)
    registry.register(User, "user.html")

    user = User(first="Ada", last="Lovelace", nickname="ada")

    assert registry.render(user) == "Ada|Ada Lovelace|ada"


def test_context_keeps_nested_models_and_methods_live() -> None:
    class Profile(BaseModel):
        name: str

        def display_name(self) -> str:
            return f"@{self.name}"

        @property
        def shouty_name(self) -> str:
            return self.name.upper()

    class UserCard(BaseModel):
        user: Profile

    env = Environment(
        loader=DictLoader(
            {"card.html": "{{ user.display_name() }} {{ user.shouty_name }}"}
        )
    )
    registry = TemplateRegistry(env)
    registry.register(UserCard, "card.html")

    card = UserCard(user=Profile(name="ada"))

    assert registry.render(card) == "@ada ADA"


def test_context_unpacks_nested_models_without_serializing_them() -> None:
    class Profile(BaseModel):
        name: str

    class UserCard(BaseModel):
        profile: Profile

    env = Environment(loader=DictLoader({"card.html": "{{ profile.name }}"}))
    registry = TemplateRegistry(env)
    registry.register(UserCard, "card.html")

    assert registry.render(UserCard(profile=Profile(name="ada"))) == "ada"


def test_render_escapes_html_from_path_constructed_registry(tmp_path: Path) -> None:
    write_template(tmp_path, "card.html", "Hello {{ name }}!")

    registry = TemplateRegistry(tmp_path)
    registry.register(Card, "card.html")

    assert registry.render(Card(name="<b>Ada</b>")) == "Hello &lt;b&gt;Ada&lt;/b&gt;!"


def test_render_response_returns_html_response_with_defaults(tmp_path: Path) -> None:
    write_template(tmp_path, "card.html", "Hello {{ name }}!")
    registry = TemplateRegistry(tmp_path)
    registry.register(Card, "card.html")

    response = registry.render_response(Card(name="Ada"))

    assert isinstance(response, HTMLResponse)
    assert response.status_code == 200
    assert response.body == b"Hello Ada!"
    assert response.headers["content-type"] == "text/html; charset=utf-8"


def test_render_response_honors_status_code_and_headers(tmp_path: Path) -> None:
    write_template(tmp_path, "card.html", "Hello {{ name }}!")
    registry = TemplateRegistry(tmp_path)
    registry.register(Card, "card.html")

    response = registry.render_response(
        Card(name="Ada"),
        status_code=201,
        headers={"HX-Trigger": "card-updated"},
    )

    assert response.status_code == 201
    assert response.headers["HX-Trigger"] == "card-updated"
    assert response.body == b"Hello Ada!"


def test_render_response_honors_media_type(tmp_path: Path) -> None:
    write_template(tmp_path, "card.html", "Hello {{ name }}!")
    registry = TemplateRegistry(tmp_path)
    registry.register(Card, "card.html")

    response = registry.render_response(Card(name="Ada"), media_type="text/plain")

    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_render_response_rejects_unregistered_model(tmp_path: Path) -> None:
    registry = TemplateRegistry(tmp_path)

    with pytest.raises(UnregisteredModelError):
        registry.render_response(Card(name="Ada"))


def test_auto_reload_true_reflects_template_edits(tmp_path: Path) -> None:
    path = write_template(tmp_path, "card.html", "v1 {{ name }}")

    registry = TemplateRegistry(tmp_path, auto_reload=True)
    registry.register(Card, "card.html")
    assert registry.render(Card(name="Ada")) == "v1 Ada"

    path.write_text("v2 {{ name }}")
    bump_mtime(path)

    assert registry.render(Card(name="Ada")) == "v2 Ada"


def test_default_auto_reload_false_uses_cached_template(tmp_path: Path) -> None:
    path = write_template(tmp_path, "card.html", "v1 {{ name }}")

    registry = TemplateRegistry(tmp_path)
    registry.register(Card, "card.html")
    assert registry.render(Card(name="Ada")) == "v1 Ada"

    path.write_text("v2 {{ name }}")
    bump_mtime(path)

    assert registry.render(Card(name="Ada")) == "v1 Ada"


def test_registry_options_are_keyword_only(tmp_path: Path) -> None:
    registry_cls: Any = TemplateRegistry
    with pytest.raises(TypeError):
        registry_cls(tmp_path, True)


def test_existing_environment_auto_reload_governs_rendering(tmp_path: Path) -> None:
    path = write_template(tmp_path, "card.html", "v1 {{ name }}")

    env = Environment(
        loader=FileSystemLoader(tmp_path),
        autoescape=select_autoescape(["html"]),
        auto_reload=True,
    )
    registry = TemplateRegistry(env)
    registry.register(Card, "card.html")
    assert registry.render(Card(name="Ada")) == "v1 Ada"

    path.write_text("v2 {{ name }}")
    bump_mtime(path)

    assert registry.render(Card(name="Ada")) == "v2 Ada"
