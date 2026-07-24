"""Shared helpers for config/detect tests."""

from __future__ import annotations

from typing import Literal
from unittest import mock

import httpx


class _FakeResp:
    """Minimal stand-in for an ``httpx.Response``."""

    def __init__(self, body: str, status: int = 200) -> None:
        self._body = body
        self.status_code = status

    @property
    def text(self) -> str:
        return self._body

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=mock.MagicMock(),
                response=self,
            )

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


_ISPDB_XML = """\
<clientConfig version="1.1">
  <emailProvider id="example.net">
    <incomingServer type="imap">
      <hostname>imap.example.net</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>smtp.example.net</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""

_MX_JSON = (
    '{"Status":0,"Answer":['
    '{"name":"example.net.","type":15,"data":"20 mx2.example.net."},'
    '{"name":"example.net.","type":15,"data":"10 mx1.example.net."}'
    "]}"
)
