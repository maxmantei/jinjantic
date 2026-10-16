import os
from pathlib import Path


def write_template(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def bump_mtime(path: Path) -> None:
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))
