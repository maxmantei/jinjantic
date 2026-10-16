"""FastAPI route decorator turning returned Pydantic models into HTML responses."""

from __future__ import annotations

import functools
import inspect
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from .exceptions import TemplateRegistrationError, UnregisteredModelError

if TYPE_CHECKING:
    from .registry import TemplateRegistry

_SEQUENCE_ORIGINS = (list, tuple, Sequence)


@dataclass(frozen=True, slots=True)
class ReturnAnnotation:
    """The Pydantic models an endpoint's return annotation may produce."""

    models: tuple[type[BaseModel], ...]
    allows_response: bool = False
    is_sequence: bool = False


def _is_response_class(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, Response)


def _sequence_models(annotation: Any) -> list[type[BaseModel]]:
    """Extract the ``BaseModel`` classes referenced by a sequence annotation."""
    models: list[type[BaseModel]] = []
    for element in get_args(annotation):
        if element is Ellipsis:
            continue
        if isinstance(element, type) and issubclass(element, BaseModel):
            models.append(element)
            continue
        if get_origin(element) in (types.UnionType, Union):
            union_models: list[type[BaseModel]] = []
            for member in get_args(element):
                if member is type(None):
                    continue
                if not (isinstance(member, type) and issubclass(member, BaseModel)):
                    raise TemplateRegistrationError(
                        f"cannot determine a template model from union member "
                        f"{member!r} in sequence return annotation "
                        f"{annotation!r}: expected a BaseModel subclass"
                    )
                union_models.append(member)
            if union_models:
                models.extend(union_models)
                continue
        raise TemplateRegistrationError(
            f"cannot determine a template model from sequence element "
            f"{element!r} of return annotation {annotation!r}: expected "
            f"BaseModel subclasses"
        )
    if not models:
        raise TemplateRegistrationError(
            f"cannot determine a template model from return annotation {annotation!r}"
        )
    return models


def analyze_return_annotation(annotation: Any) -> ReturnAnnotation:
    """Extract the ``BaseModel`` classes referenced by a return annotation."""
    if annotation is inspect.Signature.empty:
        raise TemplateRegistrationError(
            "cannot determine a template model: the route has no return "
            "annotation and no explicit model was passed to registry.endpoint"
        )
    if get_origin(annotation) in (types.UnionType, Union):
        models: list[type[BaseModel]] = []
        allows_response = False
        for member in get_args(annotation):
            if member is type(None):
                continue
            if isinstance(member, type) and issubclass(member, BaseModel):
                models.append(member)
            elif _is_response_class(member):
                allows_response = True
            else:
                raise TemplateRegistrationError(
                    f"cannot determine a template model from union member "
                    f"{member!r} of return annotation {annotation!r}: expected "
                    f"a BaseModel subclass or a Response subclass"
                )
        if not models and not allows_response:
            raise TemplateRegistrationError(
                f"cannot determine a template model from return annotation "
                f"{annotation!r}"
            )
        return ReturnAnnotation(models=tuple(models), allows_response=allows_response)
    if get_origin(annotation) in _SEQUENCE_ORIGINS:
        return ReturnAnnotation(
            models=tuple(_sequence_models(annotation)), is_sequence=True
        )
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return ReturnAnnotation(models=(annotation,))
    if _is_response_class(annotation):
        return ReturnAnnotation(models=(), allows_response=True)
    raise TemplateRegistrationError(
        f"cannot determine a template model from return annotation "
        f"{annotation!r}: expected a BaseModel subclass, a union of "
        f"BaseModels, or a Response subclass"
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


def _is_renderable_sequence(result: Any) -> bool:
    return isinstance(result, Sequence) and not isinstance(
        result, str | bytes | bytearray
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
    if isinstance(result, BaseModel) or _is_renderable_sequence(result):
        kwargs: dict[str, Any] = {"status_code": status_code, "headers": headers}
        if media_type is not None:
            kwargs["media_type"] = media_type
        return registry.render_response(result, **kwargs)
    raise TypeError(
        f"endpoint decorated with registry.endpoint must return a registered "
        f"BaseModel, a Sequence of BaseModels, or a Response, "
        f"got {type(result).__qualname__}"
    )
