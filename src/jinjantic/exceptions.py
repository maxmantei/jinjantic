class JinjanticError(Exception):
    """Base exception for all jinjantic errors."""


class TemplateRegistrationError(JinjanticError):
    """Raised when a model cannot be bound to a template."""


class UnregisteredModelError(JinjanticError):
    """Raised when rendering a model whose class is not registered."""
