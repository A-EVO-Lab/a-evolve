"""Module-level registries mapping atom names to instances.

``READERS`` / ``OPERATORS`` / ``VERIFIERS`` are shared, process-global
lookup tables. The controller emits a ``Plan`` containing atom names; the
engine resolves those names through these dicts.

Atoms register themselves at import time via :func:`register_reader` /
:func:`register_operator` / :func:`register_verifier` decorators. Re-registering
an existing name raises ``ValueError`` to catch silent overwrites — a common
bug when two modules pick the same atom name.

Lookups use :func:`get_reader` / :func:`get_operator` / :func:`get_verifier`,
which raise ``KeyError`` with an enumerated list of available names so the
controller's rule table can be corrected quickly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, TypeVar

from .interfaces import Operator, Reader, Verifier

if TYPE_CHECKING:
    pass


READERS: dict[str, Reader] = {}
OPERATORS: dict[str, Operator] = {}
VERIFIERS: dict[str, Verifier] = {}


_R = TypeVar("_R")


def _register(
    registry: dict[str, _R],
    kind: str,
    protocol: type,
    name: str,
    instance: _R,
) -> _R:
    if name in registry:
        raise ValueError(
            f"{kind} {name!r} is already registered. "
            "Re-registering an atom name is forbidden; pick a unique name "
            "or deregister the existing atom first."
        )
    if not isinstance(instance, protocol):
        raise TypeError(
            f"{kind} {name!r} does not satisfy the {protocol.__name__} protocol. "
            f"Check that the class implements the required method signature."
        )
    registry[name] = instance
    return instance


def register_reader(name: str) -> Callable[[type], type]:
    """Decorator: register a Reader class under ``name``. Instantiates the class."""

    def _wrap(cls: type) -> type:
        _register(READERS, "Reader", Reader, name, cls())
        return cls

    return _wrap


def register_operator(name: str) -> Callable[[type], type]:
    """Decorator: register an Operator class under ``name``. Instantiates the class."""

    def _wrap(cls: type) -> type:
        _register(OPERATORS, "Operator", Operator, name, cls())
        return cls

    return _wrap


def register_verifier(name: str) -> Callable[[type], type]:
    """Decorator: register a Verifier class under ``name``. Instantiates the class."""

    def _wrap(cls: type) -> type:
        _register(VERIFIERS, "Verifier", Verifier, name, cls())
        return cls

    return _wrap


def get_reader(name: str) -> Reader:
    """Look up a reader by name. Raises ``KeyError`` with available names."""
    try:
        return READERS[name]
    except KeyError:
        raise KeyError(
            f"No reader registered as {name!r}. "
            f"Available readers: {sorted(READERS)}"
        ) from None


def get_operator(name: str) -> Operator:
    """Look up an operator by name. Raises ``KeyError`` with available names."""
    try:
        return OPERATORS[name]
    except KeyError:
        raise KeyError(
            f"No operator registered as {name!r}. "
            f"Available operators: {sorted(OPERATORS)}"
        ) from None


def get_verifier(name: str) -> Verifier:
    """Look up a verifier by name. Raises ``KeyError`` with available names."""
    try:
        return VERIFIERS[name]
    except KeyError:
        raise KeyError(
            f"No verifier registered as {name!r}. "
            f"Available verifiers: {sorted(VERIFIERS)}"
        ) from None


__all__ = [
    "OPERATORS",
    "READERS",
    "VERIFIERS",
    "get_operator",
    "get_reader",
    "get_verifier",
    "register_operator",
    "register_reader",
    "register_verifier",
]
