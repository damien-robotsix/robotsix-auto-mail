"""Protocol describing the BoardHandler interface expected by server mixins."""

from __future__ import annotations

from typing import Protocol


class BoardHandlerProtocol(Protocol):
    """Structural interface that every server mixin expects from BoardHandler."""

    db_path: str
    mail_config: object | None  # MailConfig | None
    accounts: object | None  # MailAccountsConfig | None
    _current_account_id: str | None
    _aggregate: bool
    _account_cookie: str | None
    default_account_id: str | None

    def _send_response(
        self,
        body: bytes | str,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        pass

    def _redirect(self, location: str, code: int = 301) -> None:
        pass

    def _not_found(self) -> None:
        pass

    def _bad_request(self, message: str) -> None:
        pass

    def _serve_json(self, payload: object, status: int = 200) -> None:
        pass
