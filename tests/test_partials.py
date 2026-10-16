import os
from pathlib import Path

import pytest
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from jinjantic import (
    BlockNotFoundError,
    TemplateRegistrationError,
    TemplateRegistry,
    UnregisteredModelError,
)

PAGE = "<body>\n{% block row %}<li>{{ name }}</li>{% endblock %}\n</body>"

BASE = "<html>\nBASE-MARKER\n{% block content %}{% endblock %}\n</html>"

CHILD = (
    '{% extends "base.html" %}\n{% block content %}<li>{{ name }}</li>{% endblock %}'
)


def write_template(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def bump_mtime(path: Path) -> None:
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))


class Row(BaseModel):
    name: str


def test_register_with_block_fragment_path_renders_block(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "page.html#row")

    assert registry.render(Row(name="Ada")) == "<li>Ada</li>"


def test_register_with_block_keyword_renders_block(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "page.html", block="row")

    assert registry.render(Row(name="Ada")) == "<li>Ada</li>"


def test_template_decorator_with_block_fragment_renders_block(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)

    @registry.template("page.html#row")
    class DecoratedRow(BaseModel):
        name: str

    assert registry.render(DecoratedRow(name="Ada")) == "<li>Ada</li>"


def test_template_decorator_with_block_keyword_renders_block(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)

    @registry.template("page.html", block="row")
    class DecoratedRow(BaseModel):
        name: str

    assert registry.render(DecoratedRow(name="Ada")) == "<li>Ada</li>"


def test_conflicting_block_fragment_and_keyword_raise(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)

    with pytest.raises(TemplateRegistrationError):
        registry.register(Row, "page.html#row", block="other")


def test_conflicting_block_arguments_raise_via_decorator(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)

    with pytest.raises(TemplateRegistrationError):

        @registry.template("page.html#row", block="other")
        class ConflictedRow(BaseModel):
            name: str


def test_matching_block_fragment_and_keyword_are_accepted(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "page.html#row", block="row")

    assert registry.render(Row(name="Ada")) == "<li>Ada</li>"


def test_block_partial_does_not_execute_parent_layout(tmp_path: Path) -> None:
    write_template(tmp_path, "base.html", BASE)
    write_template(tmp_path, "child.html", CHILD)
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "child.html#content")

    html = registry.render(Row(name="Ada"))

    assert html == "<li>Ada</li>"
    assert "BASE-MARKER" not in html


def test_block_partial_still_escapes_html(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "page.html#row")

    html = registry.render(Row(name="<b>Ada</b>"))

    assert html == "<li>&lt;b&gt;Ada&lt;/b&gt;</li>"


def test_missing_block_raises_eagerly(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)

    with pytest.raises(BlockNotFoundError):
        registry.register(Row, "page.html#missing")


def test_deferred_validation_reports_missing_block(tmp_path: Path) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path, validate_on_register=False)
    registry.register(Row, "page.html#missing")

    with pytest.raises(BlockNotFoundError):
        registry.validate()


def test_deferred_validation_passes_once_block_exists(tmp_path: Path) -> None:
    registry = TemplateRegistry(tmp_path, validate_on_register=False)
    registry.register(Row, "page.html#row")
    write_template(tmp_path, "page.html", PAGE)

    registry.validate()

    assert registry.render(Row(name="Ada")) == "<li>Ada</li>"


def test_render_raises_block_not_found_when_not_eagerly_validated(
    tmp_path: Path,
) -> None:
    write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path, validate_on_register=False)
    registry.register(Row, "page.html#missing")

    with pytest.raises(BlockNotFoundError):
        registry.render(Row(name="Ada"))


class Toast(BaseModel):
    message: str


def test_sequence_render_concatenates_fragments_in_order(tmp_path: Path) -> None:
    write_template(tmp_path, "row.html", "<li>{{ name }}</li>")
    write_template(tmp_path, "toast.html", "<div>{{ message }}</div>")
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "row.html")
    registry.register(Toast, "toast.html")

    html = registry.render([Row(name="Ada"), Toast(message="Saved"), Row(name="Grace")])

    assert html == "<li>Ada</li><div>Saved</div><li>Grace</li>"


def test_sequence_render_accepts_tuples(tmp_path: Path) -> None:
    write_template(tmp_path, "row.html", "<li>{{ name }}</li>")
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "row.html")

    html = registry.render((Row(name="Ada"), Row(name="Grace")))

    assert html == "<li>Ada</li><li>Grace</li>"


def test_sequence_render_rejects_unregistered_element(tmp_path: Path) -> None:
    write_template(tmp_path, "row.html", "<li>{{ name }}</li>")
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "row.html")

    class OtherRow(BaseModel):
        name: str

    with pytest.raises(UnregisteredModelError):
        registry.render([Row(name="Ada"), OtherRow(name="Grace")])


def test_string_argument_is_not_treated_as_a_sequence(tmp_path: Path) -> None:
    write_template(tmp_path, "row.html", "<li>{{ name }}</li>")
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "row.html")

    with pytest.raises(UnregisteredModelError):
        registry.render("row.html")  # ty: ignore[invalid-argument-type]


def test_render_response_concatenates_sequence_fragments(tmp_path: Path) -> None:
    write_template(tmp_path, "row.html", "<li>{{ name }}</li>")
    write_template(tmp_path, "toast.html", "<div>{{ message }}</div>")
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "row.html")
    registry.register(Toast, "toast.html")

    response = registry.render_response([Row(name="Ada"), Toast(message="Saved")])

    assert isinstance(response, HTMLResponse)
    assert response.body == b"<li>Ada</li><div>Saved</div>"


def test_auto_reload_true_reflects_block_edits(tmp_path: Path) -> None:
    path = write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path, auto_reload=True)
    registry.register(Row, "page.html#row")
    assert registry.render(Row(name="Ada")) == "<li>Ada</li>"

    path.write_text("<body>\n{% block row %}<tr>{{ name }}</tr>{% endblock %}\n</body>")
    bump_mtime(path)

    assert registry.render(Row(name="Ada")) == "<tr>Ada</tr>"


def test_default_auto_reload_false_uses_cached_block(tmp_path: Path) -> None:
    path = write_template(tmp_path, "page.html", PAGE)
    registry = TemplateRegistry(tmp_path)
    registry.register(Row, "page.html#row")
    assert registry.render(Row(name="Ada")) == "<li>Ada</li>"

    path.write_text("<body>\n{% block row %}<tr>{{ name }}</tr>{% endblock %}\n</body>")
    bump_mtime(path)

    assert registry.render(Row(name="Ada")) == "<li>Ada</li>"
