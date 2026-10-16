"""FastAPI route decorator turning returned Pydantic models into HTML responses."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from .exceptions import TemplateRegistrationError, UnregisteredModelError

if TYPE_CHECKING:
    from .registry import TemplateRegistry


@dataclass(frozen=True, slots=True)
class ReturnAnnotation:
    """The Pydantic models an endpoint's return annotation may produce."""

    models: tuple[type[BaseModel], ...]


def analyze_return_annotation(annotation: Any) -> ReturnAnnotation:
    """Extract the ``BaseModel`` classes referenced by a return annotation."""
    if annotation is inspect.Signature.empty:
        raise TemplateRegistrationError(
            "cannot determine a template model: the route has no return "
            "annotation and no explicit model was passed to registry.endpoint"
        )
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return ReturnAnnotation(models=(annotation,))
    raise TemplateRegistrationError(
        f"cannot determine a template model from return annotation "
        f"{annotation!r}: expected a single BaseModel subclass"
    )


class EndpointDecorator:
    """Callable bound to a registry as ``registry.endpoint``.

    Supports the bare ``@registry.endpoint`` form and the parameterized
    ``@registry.endpoint(model, *, status_code, headers, media_type)`` form.
    """

    def __init__(self, registry: TemplateRegistry) -> None:
        self._registry = registry

    def __call__(
        self,
        fn_or_model: Callable[..., Any] | type[BaseModel] | None = None,
        *,
        model: type[BaseModel] | None = None,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> Any:
        if fn_or_model is not None and not (
            isinstance(fn_or_model, type) and issubclass(fn_or_model, BaseModel)
        ):
            return self._decorate(
                fn_or_model,
                model=model,
                status_code=status_code,
                headers=headers,
                media_type=media_type,
            )
        resolved = model or fn_or_model

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return self._decorate(
                fn,
                model=resolved,
                status_code=status_code,
                headers=headers,
                media_type=media_type,
            )

        return decorator

    def _decorate(
        self,
        fn: Callable[..., Any],
        *,
        model: type[BaseModel] | None,
        status_code: int,
        headers: Mapping[str, str] | None,
        media_type: str | None,
    ) -> Callable[..., Any]:
        signature = inspect.signature(fn)
        if model is not None:
            models = (model,)
        else:
            models = analyze_return_annotation(signature.return_annotation).models
        self._validate_registered(models)

        def render(result: Any) -> Response:
            return _to_response(
                result,
                registry=self._registry,
                status_code=status_code,
                headers=headers,
                media_type=media_type,
            )

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Response:
                return render(await fn(*args, **kwargs))

        else:

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Response:
                return render(fn(*args, **kwargs))

        cast(Any, wrapper).__signature__ = signature.replace(
            return_annotation=HTMLResponse
        )
        wrapper.__annotations__ = {**fn.__annotations__, "return": HTMLResponse}
        return wrapper

    def _validate_registered(self, models: tuple[type[BaseModel], ...]) -> None:
        for model in models:
            if model not in self._registry._bindings:
                raise UnregisteredModelError(
                    f"{model.__qualname__} is not registered in this TemplateRegistry"
                )


def _to_response(
    result: Any,
    *,
    registry: TemplateRegistry,
    status_code: int,
    headers: Mapping[str, str] | None,
    media_type: str | None,
) -> Response:
    if isinstance(result, Response):
        return result
    if isinstance(result, BaseModel):
        kwargs: dict[str, Any] = {"status_code": status_code, "headers": headers}
        if media_type is not None:
            kwargs["media_type"] = media_type
        return registry.render_response(result, **kwargs)
    raise TypeError(
        f"endpoint decorated with registry.endpoint must return a registered "
        f"BaseModel or a Response, got {type(result).__qualname__}"
    )
