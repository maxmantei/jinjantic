from pathlib import Path

import pytest
from jinja2 import DictLoader, Environment
from pydantic import BaseModel

from jinjantic import TemplateRegistry, UnregisteredModelError


def write_template(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


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
