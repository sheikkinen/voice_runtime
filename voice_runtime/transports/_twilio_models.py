"""Typed contracts for explicit Twilio REST operations."""

from pydantic import BaseModel


class TwilioRegion(BaseModel):
    """Explicit routing, account path and authentication identity."""

    api_host: str
    account_sid: str
    auth: tuple[str, str]


class TwilioCallNotFoundError(RuntimeError):
    """No supplied region provided positive terminal evidence."""

    def __init__(self, attempted_hosts: tuple[str, ...]):
        self.attempted_hosts = attempted_hosts
        super().__init__(f"Call not found in: {', '.join(attempted_hosts)}")
