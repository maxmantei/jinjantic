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
        signature = _resolved_signature(fn)
        if model is not None:
            models = (model,)
        else:
            models = analyze_return_annotation(signature.return_annotation).models
        self._validate_registered(models)
        response_param = _response_param_name(signature)

        def render(result: Any, injected: Response | None = None) -> Response:
            return _to_response(
                result,
                registry=self._registry,
                status_code=status_code,
                headers=headers,
                media_type=media_type,
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
            if model not in self._registry._bindings:
                raise UnregisteredModelError(
                    f"{model.__qualname__} is not registered in this TemplateRegistry"
                )


def _is_renderable_sequence(result: Any) -> bool:
    return isinstance(result, Sequence) and not isinstance(
        result, str | bytes | bytearray
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
    status_code: int,
    headers: Mapping[str, str] | None,
    media_type: str | None,
    injected: Response | None = None,
) -> Response:
    if isinstance(result, Response):
        return result
    if isinstance(result, BaseModel) or _is_renderable_sequence(result):
        kwargs: dict[str, Any] = {"status_code": status_code, "headers": headers}
        if media_type is not None:
            kwargs["media_type"] = media_type
        rendered = registry.render_response(result, **kwargs)
        if injected is not None:
            _merge_injected(rendered, injected)
        return rendered
    raise TypeError(
        f"endpoint decorated with registry.endpoint must return a registered "
        f"BaseModel, a Sequence of BaseModels, or a Response, "
        f"got {type(result).__qualname__}"
    )
