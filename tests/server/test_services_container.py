"""Unit tests for the composition-foundation service container and context.

Covers the stateless-service wiring in ``_services`` and the request-context
abstraction in ``_board_handler_protocol`` that the mixin→service migration
tickets build on, plus ``BoardHandler``'s construction of the container
before request dispatch.
"""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.server import BoardHandler
from robotsix_auto_mail.server._board_handler_protocol import (
    BoardHandlerProtocol,
    RequestContext,
)
from robotsix_auto_mail.server._services import Service, ServiceContainer


class _SampleService(Service):
    """A trivial stateless service used to exercise the container."""


class TestService:
    def test_stores_injected_dependencies(self) -> None:
        svc = Service(db_path="a.db", mail_config="cfg", accounts="accts")
        assert svc.db_path == "a.db"
        assert svc.mail_config == "cfg"
        assert svc.accounts == "accts"

    def test_defaults_are_none(self) -> None:
        svc = Service(db_path="a.db")
        assert svc.mail_config is None
        assert svc.accounts is None


class TestServiceContainer:
    def test_stores_injected_dependencies(self) -> None:
        container = ServiceContainer(
            db_path="a.db", mail_config="cfg", accounts="accts"
        )
        assert container.db_path == "a.db"
        assert container.mail_config == "cfg"
        assert container.accounts == "accts"

    def test_get_builds_service_wired_with_dependencies(self) -> None:
        container = ServiceContainer(
            db_path="a.db", mail_config="cfg", accounts="accts"
        )
        svc = container.get(_SampleService)
        assert isinstance(svc, _SampleService)
        assert svc.db_path == "a.db"
        assert svc.mail_config == "cfg"
        assert svc.accounts == "accts"

    def test_get_memoises_instance(self) -> None:
        container = ServiceContainer(db_path="a.db")
        assert container.get(_SampleService) is container.get(_SampleService)

    def test_get_returns_distinct_instances_per_class(self) -> None:
        class _Other(Service):
            pass

        container = ServiceContainer(db_path="a.db")
        assert container.get(_SampleService) is not container.get(_Other)


class TestRequestContext:
    def test_alias_points_at_board_handler_protocol(self) -> None:
        assert RequestContext is BoardHandlerProtocol


class TestBoardHandlerWiring:
    """The container must be built before ``super().__init__`` dispatches."""

    def test_init_builds_service_container_wired_with_dependencies(self) -> None:
        # Patch the real transport __init__ (``socketserver.BaseRequestHandler``
        # synchronously calls ``handle()``) so we can inspect the instance
        # without a live socket.
        with mock.patch(
            "socketserver.BaseRequestHandler.__init__", return_value=None
        ) as super_init:
            handler = BoardHandler.__new__(BoardHandler)
            BoardHandler.__init__(
                handler,
                db_path="wired.db",
                mail_config=None,
                accounts=None,
            )

        super_init.assert_called_once()
        assert isinstance(handler._services, ServiceContainer)
        assert handler._services.db_path == "wired.db"
        assert handler._services.mail_config is None
        assert handler._services.accounts is None
