from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from pydantic import BaseModel

from .endpoint import EndpointDecorator
from .exceptions import (
    BlockNotFoundError,
    TemplateRegistrationError,
    UnregisteredModelError,
)

type Renderable = BaseModel | Sequence[BaseModel]


def _parse_path(path: str, block: str | None) -> tuple[str, str | None]:
    """Split ``"path.html#block"`` and reconcile it with ``block=...``."""
    template_name, separator, fragment = path.partition("#")
    if not separator:
        return path, block
    if block is not None and block != fragment:
        raise TemplateRegistrationError(
            f"Conflicting block names for {path!r}: "
            f"path names {fragment!r} but block={block!r} was passed"
        )
    return template_name, fragment


@dataclass(slots=True)
class TemplateBinding:
    """Internal binding of a Pydantic model class to a Jinja2 template."""

    model: type[BaseModel]
    template_name: str
    template: Template | None = None
    block_name: str | None = None


class TemplateRegistry:
    """Binds Pydantic models to Jinja2 templates and renders them."""

    def __init__(
        self,
        env_or_directory: Environment | str | Path | Sequence[str | Path],
        *,
        auto_reload: bool = False,
        validate_on_register: bool = True,
    ) -> None:
        if isinstance(env_or_directory, Environment):
            self.env = env_or_directory
        else:
            if isinstance(env_or_directory, str | Path):
                paths: Sequence[str | Path] = [env_or_directory]
            else:
                paths = env_or_directory
            self.env = Environment(
                loader=FileSystemLoader(paths),
                autoescape=select_autoescape(["html", "xml"]),
                auto_reload=auto_reload,
            )
        self._validate_on_register = validate_on_register
        self._bindings: dict[type[BaseModel], TemplateBinding] = {}

    @property
    def endpoint(self) -> EndpointDecorator:
        """FastAPI route decorator rendering returned registered models."""
        return EndpointDecorator(self)

    def template[M: BaseModel](
        self, path: str, *, block: str | None = None
    ) -> Callable[[type[M]], type[M]]:
        """Class decorator binding a Pydantic model to a template."""

        def decorator(model: type[M]) -> type[M]:
            self.register(model, path, block=block)
            return model

        return decorator

    def register[M: BaseModel](
        self, model: type[M], path: str, *, block: str | None = None
    ) -> type[M]:
        """Bind ``model`` to the template at ``path`` and return the model class."""
        if model in self._bindings:
            raise TemplateRegistrationError(
                f"{model.__qualname__} is already registered in this TemplateRegistry"
            )
        template_name, block_name = _parse_path(path, block)
        template = (
            self._resolve(template_name, block_name)
            if self._validate_on_register
            else None
        )
        self._bindings[model] = TemplateBinding(
            model=model,
            template_name=template_name,
            template=template,
            block_name=block_name,
        )
        return model

    def validate(self) -> None:
        """Validate every registered binding, resolving its template."""
        for binding in self._bindings.values():
            binding.template = self._resolve(binding.template_name, binding.block_name)

    def render(self, data: Renderable) -> str:
        """Render registered model instance(s) to HTML synchronously."""
        if isinstance(data, BaseModel):
            return self._render_one(data)
        if isinstance(data, str | bytes | bytearray):
            raise UnregisteredModelError(
                f"{type(data).__qualname__} is not registered in this TemplateRegistry"
            )
        return "".join(self._render_one(model) for model in data)

    def _render_one(self, data: BaseModel) -> str:
        binding = self._bindings.get(type(data))
        if binding is None:
            raise UnregisteredModelError(
                f"{type(data).__qualname__} is not registered in this TemplateRegistry"
            )
        if binding.template is None or self.env.auto_reload:
            binding.template = self._resolve(binding.template_name, binding.block_name)
        template = binding.template
        if binding.block_name is None:
            return template.render(self._context(data))
        return template.environment.concat(
            template.blocks[binding.block_name](
                template.new_context(self._context(data))
            )
        )

    def _resolve(self, template_name: str, block_name: str | None) -> Template:
        template = self.env.get_template(template_name)
        self._require_block(template, template_name, block_name)
        return template

    @staticmethod
    def _require_block(
        template: Template, template_name: str, block_name: str | None
    ) -> None:
        if block_name is not None and block_name not in template.blocks:
            raise BlockNotFoundError(
                f"Template {template_name!r} has no block named {block_name!r}"
            )

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
