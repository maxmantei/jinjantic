# jinjantic

Typed Jinja2 templates for FastAPI powered by Pydantic models.

## Installation

Install a pinned release (recommended):

```bash
uv add "jinjantic @ git+https://github.com/maxmantei/jinjantic.git@v0.1.0"
```

Or follow the latest stable release (`main` only advances when a release is tagged):

```bash
uv add "jinjantic @ git+https://github.com/maxmantei/jinjantic.git@main"
```

Always specify a ref: an install without one resolves the repository's default branch (`dev`), which is under active development.

If you use AI coding agents, link the bundled library skill into your project with [Library Skills](https://library-skills.io):

```bash
uvx library-skills
```

## Examples

Assuming a `templates/users.html` file:

```html
<article>
  <h1>{{ name }}</h1>
  <ul>
    {% block row %}<li>{{ name }}</li>{% endblock %}
  </ul>
</article>
```

### 1. FastAPI route decorator

Bind a Pydantic model to a template (or a `{% block %}` fragment using `#block`) with `@registry.template`, then decorate your FastAPI route with `@registry.endpoint`:

```python
from fastapi import FastAPI
from jinjantic import TemplateRegistry
from pydantic import BaseModel

app = FastAPI()
registry = TemplateRegistry("templates")


@registry.template("users.html")
class UserPage(BaseModel):
    name: str


@app.get("/users/{name}")
@registry.endpoint
async def get_user(name: str) -> UserPage:
    return UserPage(name=name)
```

`@registry.endpoint` validates at decoration time that `UserPage` is registered, renders the returned model into an `HTMLResponse`, and documents the route as returning HTML in OpenAPI. It also supports union returns (`UserPage | ErrorNotice`), sequences (`list[UserRow]`), and pass-through `Response` objects (`UserPage | RedirectResponse`).

### 2. Rendering HTML directly (e.g. in a WebSocket)

Use `registry.render()` to render a registered model—or a sequence of models—directly to an HTML `str`, such as when pushing fragments over a WebSocket or Server-Sent Events (SSE):

```python
from fastapi import WebSocket


@registry.template("users.html#row")
class UserRow(BaseModel):
    name: str


@app.websocket("/ws/users")
async def stream_users(websocket: WebSocket) -> None:
    await websocket.accept()

    # Render a single block partial
    await websocket.send_text(registry.render(UserRow(name="Ada")))

    # Render and concatenate multiple fragments in one message
    await websocket.send_text(registry.render([UserRow(name="Grace"), UserRow(name="Margaret")]))

    await websocket.close()
```
