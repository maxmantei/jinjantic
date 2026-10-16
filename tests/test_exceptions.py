from jinjantic import (
    BlockNotFoundError,
    JinjanticError,
    TemplateRegistrationError,
    UnregisteredModelError,
)


def test_exception_hierarchy() -> None:
    assert issubclass(JinjanticError, Exception)
    assert issubclass(TemplateRegistrationError, JinjanticError)
    assert issubclass(UnregisteredModelError, JinjanticError)
    assert issubclass(BlockNotFoundError, JinjanticError)
