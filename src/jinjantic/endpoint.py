"""FastAPI route decorator turning returned Pydantic models into HTML responses."""

from __future__ import annotations

import functools
import inspect
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeGuard, Union, cast, get_args, get_origin

from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from .exceptions import TemplateRegistrationError, UnregisteredModelError

if TYPE_CHECKING:
    from .registry import TemplateRegistry

_SEQUENCE_ORIGINS = (list, tuple, Sequence)


@dataclass(frozen=True, slots=True)
class _ResponseOptions:
    """Response metadata applied to rendered endpoint results."""

    status_code: int = 200
    headers: Mapping[str, str] | None = None
    media_type: str | None = None


def _is_model_class(annotation: Any) -> TypeGuard[type[BaseModel]]:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _is_response_class(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, Response)


def _is_union(annotation: Any) -> bool:
    return get_origin(annotation) in (types.UnionType, Union)


def _no_models(annotation: Any, expected: str) -> TemplateRegistrationError:
    return TemplateRegistrationError(
        f"cannot determine a template model from return annotation "
        f"{annotation!r}: expected {expected}"
    )


def _union_models(annotation: Any, *, allow_response: bool) -> list[type[BaseModel]]:
    """Collect the ``BaseModel`` classes referenced by a union annotation."""
    models: list[type[BaseModel]] = []
    for member in get_args(annotation):
        if member is type(None):
            raise TemplateRegistrationError(
                f"cannot determine a template model from union member NoneType "
                f"of return annotation {annotation!r}: optional returns are not "
                f"supported, pass an explicit model to registry.endpoint instead"
            )
        if _is_model_class(member):
            models.append(member)
            continue
        if allow_response and _is_response_class(member):
            continue
        expected = (
            "a BaseModel subclass or a Response subclass"
            if allow_response
            else "a BaseModel subclass"
        )
        raise TemplateRegistrationError(
            f"cannot determine a template model from union member {member!r} "
            f"of return annotation {annotation!r}: expected {expected}"
        )
    return models


def _sequence_models(annotation: Any) -> list[type[BaseModel]]:
    """Extract the ``BaseModel`` classes referenced by a sequence annotation."""
    models: list[type[BaseModel]] = []
    for element in get_args(annotation):
        if element is Ellipsis:
            continue
        if _is_model_class(element):
            models.append(element)
            continue
        if _is_union(element):
            models.extend(_union_models(element, allow_response=False))
            continue
        raise TemplateRegistrationError(
            f"cannot determine a template model from sequence element "
            f"{element!r} of return annotation {annotation!r}: expected "
            f"BaseModel subclasses"
        )
    if not models:
        raise _no_models(annotation, "at least one BaseModel subclass")
    return models


def _analyze_return_annotation(annotation: Any) -> tuple[type[BaseModel], ...]:
    """Extract the ``BaseModel`` classes referenced by a return annotation."""
    if annotation is inspect.Signature.empty:
        raise TemplateRegistrationError(
            "cannot determine a template model: the route has no return "
            "annotation and no explicit model was passed to registry.endpoint"
        )
    if _is_union(annotation):
        models = _union_models(annotation, allow_response=True)
        if not models:
            raise _no_models(
                annotation,
                "at least one BaseModel subclass, or pass an explicit model "
                "to registry.endpoint for routes that only return Response "
                "objects",
            )
        return tuple(models)
    if get_origin(annotation) in _SEQUENCE_ORIGINS:
        return tuple(_sequence_models(annotation))
    if _is_model_class(annotation):
        return (annotation,)
    if isinstance(annotation, str):
        raise TemplateRegistrationError(
            f"return annotation {annotation!r} could not be resolved to a "
            f"model: if the model is defined in a local scope, pass an "
            f"explicit model to registry.endpoint"
        )
    if _is_response_class(annotation):
        raise _no_models(
            annotation,
            "a BaseModel subclass, or pass an explicit model to "
            "registry.endpoint for routes that only return Response objects",
        )
    raise _no_models(
        annotation,
        "a BaseModel subclass, a union of BaseModels, or a Response subclass",
    )


class _EndpointDecorator:
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
        options = _ResponseOptions(
            status_code=status_code, headers=headers, media_type=media_type
        )
        if fn_or_model is None:
            resolved = model
        elif _is_model_class(fn_or_model):
            resolved = model or fn_or_model
        else:
            return self._decorate(fn_or_model, model=model, options=options)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return self._decorate(fn, model=resolved, options=options)

        return decorator

    def _decorate(
        self,
        fn: Callable[..., Any],
        *,
        model: type[BaseModel] | None,
        options: _ResponseOptions,
    ) -> Callable[..., Any]:
        signature = _resolved_signature(fn)
        if model is not None:
            models = (model,)
        else:
            models = _analyze_return_annotation(signature.return_annotation)
        self._validate_registered(models)
        response_param = _response_param_name(signature)

        def render(result: Any, injected: Response | None = None) -> Response:
            return _to_response(
                result,
                registry=self._registry,
                options=options,
                injected=injected,
            )

        def injected_response(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if response_param is None:
                return None
            return _find_argument(signature, response_param, args, kwargs)

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Response:
                injected = injected_response(args, kwargs)
                return render(await fn(*args, **kwargs), injected)

        else:

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Response:
                injected = injected_response(args, kwargs)
                return render(fn(*args, **kwargs), injected)

        cast(Any, wrapper).__signature__ = signature.replace(
            return_annotation=HTMLResponse
        )
        wrapper.__annotations__ = {**fn.__annotations__, "return": HTMLResponse}
        return wrapper

    def _validate_registered(self, models: tuple[type[BaseModel], ...]) -> None:
        for model in models:
            if not self._registry.is_registered(model):
                raise UnregisteredModelError(
                    f"{model.__qualname__} is not registered in this TemplateRegistry"
                )


def _resolved_signature(fn: Callable[..., Any]) -> inspect.Signature:
    """Return the signature with string annotations evaluated if possible.

    Handles postponed annotations (``from __future__ import annotations``).
    Falls back to the raw signature when an annotation cannot be evaluated,
    for example a model defined in a local scope.
    """
    try:
        return inspect.signature(fn, eval_str=True)
    except NameError, TypeError, SyntaxError, AttributeError:
        return inspect.signature(fn)


def _response_param_name(signature: inspect.Signature) -> str | None:
    """Name of the parameter FastAPI injects a ``Response`` into, if any."""
    for name, parameter in signature.parameters.items():
        if _is_response_class(parameter.annotation):
            return name
    return None


def _find_argument(
    signature: inspect.Signature,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    if name in kwargs:
        return kwargs[name]
    index = list(signature.parameters).index(name)
    return args[index] if index < len(args) else None


def _merge_injected(response: HTMLResponse, injected: Response) -> None:
    """Copy mutations made on an injected ``Response`` onto ``response``.

    Injected headers overwrite same-named headers on ``response``; cookies
    are kept as separate ``set-cookie`` headers.
    """
    for key, value in injected.raw_headers:
        name = key.lower()
        if name in (b"content-length", b"content-type"):
            continue
        if name == b"set-cookie":
            response.raw_headers.append((key, value))
        else:
            response.headers[key.decode("latin-1")] = value.decode("latin-1")
    if injected.status_code is not None:
        response.status_code = injected.status_code


def _to_response(
    result: Any,
    *,
    registry: TemplateRegistry,
    options: _ResponseOptions,
    injected: Response | None = None,
) -> Response:
    if isinstance(result, Response):
        return result
    rendered = registry.render_response(
        result,
        status_code=options.status_code,
        headers=options.headers,
        media_type=options.media_type,
    )
    if injected is not None:
        _merge_injected(rendered, injected)
    return rendered
