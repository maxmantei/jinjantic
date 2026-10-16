from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from pydantic import BaseModel

from .endpoint import EndpointDecorator
from .exceptions import TemplateRegistrationError, UnregisteredModelError


@dataclass(slots=True)
class TemplateBinding:
    """Internal binding of a Pydantic model class to a Jinja2 template."""

    model: type[BaseModel]
    template_name: str
    template: Template | None = None


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

    def template[M: BaseModel](self, path: str) -> Callable[[type[M]], type[M]]:
        """Class decorator binding a Pydantic model to a template."""

        def decorator(model: type[M]) -> type[M]:
            self.register(model, path)
            return model

        return decorator

    def register[M: BaseModel](self, model: type[M], path: str) -> type[M]:
        """Bind ``model`` to the template at ``path`` and return the model class."""
        if model in self._bindings:
            raise TemplateRegistrationError(
                f"{model.__qualname__} is already registered in this TemplateRegistry"
            )
        template = self.env.get_template(path) if self._validate_on_register else None
        self._bindings[model] = TemplateBinding(
            model=model, template_name=path, template=template
        )
        return model

    def validate(self) -> None:
        """Validate every registered binding, resolving its template."""
        for binding in self._bindings.values():
            binding.template = self.env.get_template(binding.template_name)

    def render(self, data: BaseModel) -> str:
        """Render a registered model instance to HTML synchronously."""
        binding = self._bindings.get(type(data))
        if binding is None:
            raise UnregisteredModelError(
                f"{type(data).__qualname__} is not registered in this TemplateRegistry"
            )
        template = binding.template
        if self.env.auto_reload or template is None:
            template = self.env.get_template(binding.template_name)
        return template.render(self._context(data))

    def render_response(
        self,
        data: BaseModel,
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
