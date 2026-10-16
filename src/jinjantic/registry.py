from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from pydantic import BaseModel

from .endpoint import _EndpointDecorator
from .exceptions import (
    BlockNotFoundError,
    JinjanticError,
    TemplateRegistrationError,
    UnregisteredModelError,
)

type Renderable = BaseModel | Sequence[BaseModel]


class TemplateTarget(NamedTuple):
    """A template path split into a template name and an optional block name."""

    template_name: str
    block_name: str | None


def _parse_path(path: str, block: str | None) -> TemplateTarget:
    """Split ``"path.html#block"`` and reconcile it with ``block=...``."""
    template_name, separator, block_name = path.partition("#")
    if not separator:
        return TemplateTarget(path, block)
    if block is not None and block != block_name:
        raise TemplateRegistrationError(
            f"Conflicting block names for {path!r}: "
            f"path names {block_name!r} but block={block!r} was passed"
        )
    return TemplateTarget(template_name, block_name)


def _render_block(template: Template, block_name: str, context: dict[str, Any]) -> str:
    """Render the named block fragment without executing parent layouts.

    Only the block is evaluated; ``{% extends %}`` parents are not rendered
    and ``{{ super() }}`` is unavailable inside a fragment.
    """
    return template.environment.concat(
        template.blocks[block_name](template.new_context(context))
    )


@dataclass(slots=True)
class TemplateBinding:
    """Internal binding of a Pydantic model class to a Jinja2 template."""

    target: TemplateTarget
    template: Template | None = None


class TemplateRegistry:
    """Binds Pydantic models to Jinja2 templates and renders them."""

    def __init__(
        self,
        env_or_directory: Environment | str | Path | Sequence[str | Path],
        *,
        auto_reload: bool | None = None,
        validate_on_register: bool = True,
    ) -> None:
        """Create a registry from a template directory or an environment.

        When given path(s), a Jinja2 environment with HTML/XML autoescaping
        is created with ``auto_reload`` enabled only if requested. When given
        an existing environment, its own ``auto_reload`` governs rendering;
        passing ``auto_reload`` alongside an environment is an error (set it
        on the environment itself).
        """
        if isinstance(env_or_directory, Environment):
            if auto_reload is not None:
                raise JinjanticError(
                    "auto_reload cannot be passed to TemplateRegistry together "
                    "with a jinja2.Environment; set auto_reload on the "
                    "environment itself"
                )
            self.env = env_or_directory
        else:
            if isinstance(env_or_directory, str | Path):
                paths: Sequence[str | Path] = [env_or_directory]
            else:
                paths = env_or_directory
            self.env = Environment(
                loader=FileSystemLoader(paths),
                autoescape=select_autoescape(["html", "xml"]),
                auto_reload=bool(auto_reload),
            )
        self._validate_on_register = validate_on_register
        self._bindings: dict[type[BaseModel], TemplateBinding] = {}

    @property
    def endpoint(self) -> _EndpointDecorator:
        """FastAPI route decorator rendering returned registered models."""
        return _EndpointDecorator(self)

    def is_registered(self, model: type[BaseModel]) -> bool:
        """Return whether ``model`` is bound to a template in this registry."""
        return model in self._bindings

    def template[M: BaseModel](
        self, path: str, *, block: str | None = None
    ) -> Callable[[type[M]], type[M]]:
        """Class decorator binding a Pydantic model to a template.

        ``path`` may address a block as ``"page.html#block"`` or via
        ``block=``. Rendering a block fragment executes only that block;
        parent ``{% extends %}`` layouts are not rendered and ``{{ super() }}``
        is unavailable inside a fragment.
        """

        def decorator(model: type[M]) -> type[M]:
            self.register(model, path, block=block)
            return model

        return decorator

    def register[M: BaseModel](
        self, model: type[M], path: str, *, block: str | None = None
    ) -> type[M]:
        """Bind ``model`` to the template at ``path`` and return the model class.

        ``path`` may address a block as ``"page.html#block"`` or via
        ``block=``. Rendering a block fragment executes only that block;
        parent ``{% extends %}`` layouts are not rendered and ``{{ super() }}``
        is unavailable inside a fragment.
        """
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TemplateRegistrationError(
                f"registry.register() expects a Pydantic BaseModel subclass, "
                f"got {model!r}"
            )
        if model in self._bindings:
            raise TemplateRegistrationError(
                f"{model.__qualname__} is already registered in this TemplateRegistry"
            )
        target = _parse_path(path, block)
        template = self._resolve(target) if self._validate_on_register else None
        self._bindings[model] = TemplateBinding(target=target, template=template)
        return model

    def validate(self) -> None:
        """Validate every registered binding, resolving its template."""
        for binding in self._bindings.values():
            binding.template = self._resolve(binding.target)

    def render(self, data: Renderable) -> str:
        """Render registered model instance(s) to HTML synchronously."""
        if isinstance(data, BaseModel):
            return self._render_one(data)
        if isinstance(data, str | bytes | bytearray):
            raise UnregisteredModelError(
                f"render() expects a registered BaseModel instance or a "
                f"sequence of them, got {type(data).__name__}"
            )
        return "".join(self._render_one(model) for model in data)

    def _render_one(self, data: BaseModel) -> str:
        binding = self._bindings.get(type(data))
        if binding is None:
            raise UnregisteredModelError(
                f"{type(data).__qualname__} is not registered in this TemplateRegistry"
            )
        if binding.template is None or self.env.auto_reload:
            binding.template = self._resolve(binding.target)
        template = binding.template
        if binding.target.block_name is None:
            return template.render(self._context(data))
        return _render_block(template, binding.target.block_name, self._context(data))

    def _resolve(self, target: TemplateTarget) -> Template:
        template = self.env.get_template(target.template_name)
        if target.block_name is not None and target.block_name not in template.blocks:
            raise BlockNotFoundError(
                f"Template {target.template_name!r} has no block named "
                f"{target.block_name!r}"
            )
        return template

    def render_response(
        self,
        data: Renderable,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> HTMLResponse:
        """Render a registered model instance into a FastAPI ``HTMLResponse``."""
        return HTMLResponse(
            content=self.render(data),
            status_code=status_code,
            headers=headers,
            media_type=media_type,
        )

    def _context(self, data: BaseModel) -> dict[str, Any]:
        model = type(data)
        names = set(model.model_fields) | set(model.model_computed_fields)
        context = {name: getattr(data, name) for name in names}
        extra = data.__pydantic_extra__
        if extra:
            context |= {name: getattr(data, name) for name in extra}
        return context
