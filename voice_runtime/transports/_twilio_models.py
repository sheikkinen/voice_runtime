"""Typed contracts for explicit Twilio REST operations."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


def validate_sid(value: str) -> str:
    """Require a single URI-safe, strict ASCII alphanumeric SID."""
    if not isinstance(value, str) or not value.isascii() or not value.isalnum():
        raise ValueError("SID must be a nonblank ASCII alphanumeric string")
    return value


class TwilioRegion(BaseModel):
    """Explicit routing, account path and authentication identity."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    api_host: str
    account_sid: str
    auth: tuple[str, str] = Field(repr=False)

    @field_validator("api_host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if value != "api.twilio.com" and not re.fullmatch(
            rf"api\.{label}\.{label}\.twilio\.com", value
        ):
            raise ValueError("Invalid Twilio API host")
        return value

    @field_validator("account_sid")
    @classmethod
    def valid_account(cls, value: str) -> str:
        return validate_sid(value)

    @field_validator("auth")
    @classmethod
    def valid_auth(cls, value: tuple[str, str]) -> tuple[str, str]:
        if any(not part.strip() for part in value):
            raise ValueError("Authentication requires two nonblank strings")
        return value


class TwilioCallNotFoundError(RuntimeError):
    """No supplied region provided positive terminal evidence."""

    def __init__(self, attempted_hosts: tuple[str, ...]):
        self.attempted_hosts = attempted_hosts
        super().__init__(f"Call not found in: {', '.join(attempted_hosts)}")


class TwilioCallRow(BaseModel):
    """One normalized call record with explicitly nullable time and caller."""

    call_sid: str
    status: str
    start_time: float | None
    direction: str
    caller: str | None


class TwilioCallListIncompleteError(RuntimeError):
    """The region still has continuation after the request cap."""

    def __init__(self, api_host: str, pages_fetched: int):
        self.api_host = api_host
        self.pages_fetched = pages_fetched
        super().__init__(
            f"Incomplete call list from {api_host} after {pages_fetched} pages"
        )
