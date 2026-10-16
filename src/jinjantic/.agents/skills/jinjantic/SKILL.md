---
name: jinjantic
description: Bind Pydantic models to Jinja2 templates and render typed HTML in FastAPI. Use when working with jinjantic, TemplateRegistry, @registry.template, @registry.endpoint, registry.render, registry.render_response, Jinja2 block partials (e.g. for HTMX), or sending rendered HTML over WebSockets and Server-Sent Events (SSE).
---

# Jinjantic

Typed Jinja2 templates for FastAPI powered by Pydantic models.

## Quick Reference

- **Create a registry**: `registry = TemplateRegistry("templates")` (accepts a path, sequence of paths, or an existing `jinja2.Environment`).
- **Bind a model**: decorate a `BaseModel` subclass with `@registry.template("card.html")` or call `registry.register(Card, "card.html")`.
- **Bind a template block (partial)**: `@registry.template("page.html#row")` or `@registry.template("page.html", block="row")`.
- **Decorate a FastAPI route**: place `@registry.endpoint` **below** `@app.get(...)` and annotate the return type with the registered model(s).
- **Render to `str`**: `registry.render(model_or_sequence)` — ideal for WebSockets, SSE, emails, or CLI output.
- **Render to `HTMLResponse`**: `registry.render_response(model_or_sequence, status_code=200, headers={...})`.

## Creating a `TemplateRegistry`

```python
from pathlib import Path
from jinjantic import TemplateRegistry

# From a directory path (or sequence of paths)
# Enables HTML/XML autoescaping by default; auto_reload defaults to False
registry = TemplateRegistry(Path("templates"), auto_reload=True)
```

- **Existing `jinja2.Environment`**: pass the `Environment` directly (`TemplateRegistry(env)`). Do **not** pass `auto_reload` alongside an existing `Environment`—configure `auto_reload` on the `Environment` itself, or `JinjanticError` is raised.
- **Deferred validation**: by default (`validate_on_register=True`), `register()` / `@registry.template(...)` immediately resolves the template and block at registration time. Pass `validate_on_register=False` if templates are generated or loaded later, and call `registry.validate()` at startup to verify all bindings eagerly.

## Registering Models & Block Partials

Bind each Pydantic `BaseModel` subclass to a template path (or a specific `{% block %}` inside a template):

```python
from pydantic import BaseModel, computed_field


@registry.template("users/profile.html")
class UserProfile(BaseModel):
    first: str
    last: str

    @computed_field
    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"


# Block fragment: renders ONLY `{% block row %}...{% endblock %}`
@registry.template("users/profile.html#row")
class UserRow(BaseModel):
    name: str
```

Key rules:
- **One binding per model class per registry**: registering the same model class twice in the same `TemplateRegistry` raises `TemplateRegistrationError`. Multiple distinct model classes *may* share the same template or block.
- **Template context unpacking**: top-level model fields, `@computed_field` properties, and extra fields (`extra="allow"`) are unpacked directly into the Jinja2 context (`{{ first }}`, `{{ full_name }}`). Nested models are passed as live Python objects (not serialized to dicts), so their methods and `@property` attributes remain callable inside templates (`{{ user.display_name() }}`).
- **Block partial isolation**: rendering a block (`"page.html#row"` or `block="row"`) evaluates only that block. Parent `{% extends %}` layouts are not executed and `{{ super() }}` is unavailable inside a fragment.

## FastAPI Route Decorator (`@registry.endpoint`)

Place `@registry.endpoint` **underneath** the FastAPI route decorator (`@app.get`, `@app.post`, etc.). Both `async def` and sync `def` route functions are supported.

```python
from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse

app = FastAPI()


@app.get("/users/{user_id}")
@registry.endpoint
async def get_user(user_id: int) -> UserProfile:
    return UserProfile(first="Ada", last="Lovelace")
```

### Supported Return Annotations

`@registry.endpoint` inspects the route's return annotation at decoration time and verifies that every referenced `BaseModel` is registered in `registry` (raising `UnregisteredModelError` or `TemplateRegistrationError` immediately on mismatch):

1. **Single model**: `-> UserProfile`
2. **Union of models**: `-> UserProfile | ErrorAlert` — renders whichever model instance is returned at runtime.
3. **Sequences (OOB swaps / multi-fragment responses)**: `-> list[UserRow]`, `-> tuple[UserProfile, Toast]`, or `-> Sequence[UserRow | ErrorAlert]` — renders each element in order and concatenates them into a single `HTMLResponse`.
4. **Model or FastAPI `Response`**: `-> UserProfile | RedirectResponse` — if the route returns a `Response` instance (such as `RedirectResponse`), it passes through untouched.

`@registry.endpoint` rewrites the wrapper's exposed return annotation to `HTMLResponse` so FastAPI generates accurate OpenAPI documentation without leaking internal template models into the JSON schema.

### Decorator Options & Explicit Models

```python
@app.post("/users")
@registry.endpoint(status_code=201, headers={"HX-Trigger": "user-created"})
async def create_user() -> UserRow:
    return UserRow(name="Ada")
```

- **Mutating an injected `Response`**: if the route declares a `response: Response` parameter, any headers, multiple `set_cookie(...)` calls, or `status_code` changes applied to `response` are merged onto the rendered `HTMLResponse` (and ignored if a pass-through `RedirectResponse` is returned).
- **Explicit `model=`**: optional return annotations (`-> UserRow | None`), locally scoped models with unresolvable string annotations, or routes annotated only with a `Response` subclass require an explicit model: `@registry.endpoint(UserRow)` or `@registry.endpoint(model=UserRow)`.

## Direct Rendering (`render` and `render_response`)

Use `registry.render(data)` when you need a rendered HTML `str` outside a standard HTTP route—such as over WebSockets, Server-Sent Events (SSE), or background tasks. Both `render()` and `render_response()` accept a single registered `BaseModel` or any `Sequence[BaseModel]` (lists, tuples):

```python
from fastapi import WebSocket


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    # Single model -> str
    await websocket.send_text(registry.render(UserProfile(first="Ada", last="Lovelace")))
    # Sequence of models -> concatenated str (great for HTMX out-of-band swaps)
    await websocket.send_text(
        registry.render([UserRow(name="Ada"), UserRow(name="Grace")])
    )
    await websocket.close()
```

Use `registry.render_response(data, *, status_code=200, headers=None, media_type=None)` when building a FastAPI `HTMLResponse` imperatively without `@registry.endpoint`.

## Exceptions

All exceptions inherit from `JinjanticError`:
- `TemplateRegistrationError`: invalid model class, duplicate registration, conflicting block names, or unsupported/missing endpoint return annotation.
- `UnregisteredModelError`: attempting to render or decorate a route with a `BaseModel` not registered in the `TemplateRegistry`.
- `BlockNotFoundError`: the target template does not define the requested `#block`.
