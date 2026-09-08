"""No-network witnesses at requests' final prepared-request boundary."""

import json
from dataclasses import dataclass

from requests import PreparedRequest, Response, Session


@dataclass
class SentRequest:
    request: PreparedRequest
    timeout: float
    allow_redirects: bool


class FakeTwilioHTTP:
    """Run the real SDK and HTTP client, replacing only the network send."""

    def __init__(self, monkeypatch):
        self.sent: list[SentRequest] = []
        self.replies: list[Response | Exception] = []
        self.constructed = 0
        from twilio.rest import Client

        original_init = Client.__init__

        def counted_init(client, *args, **kwargs):
            self.constructed += 1
            original_init(client, *args, **kwargs)

        def send(session, request, **kwargs):
            self.sent.append(
                SentRequest(request, kwargs["timeout"], kwargs["allow_redirects"])
            )
            assert self.replies, "Unexpected HTTP request (network prohibited)"
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            reply.request = request
            return reply

        monkeypatch.setattr(Client, "__init__", counted_init)
        monkeypatch.setattr(Session, "send", send)
        for name in (
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_REGION",
            "TWILIO_EDGE",
            "TWILIO_HTTP_TIMEOUT",
        ):
            monkeypatch.delenv(name, raising=False)

    def reply(self, status=200, payload=None, *, text=None, headers=None):
        response = Response()
        response.status_code = status
        response._content = (
            text
            if text is not None
            else json.dumps(payload if payload is not None else {})
        ).encode()
        response.headers.update(headers or {})
        self.replies.append(response)
