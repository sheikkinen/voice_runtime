# NC-285: Route Token Stream XML Builder

**Status:** Enforced - 2026-05-11
**Effort:** 0.25 day
**Requested:** 2026-05-11
**Blocks:** ninchat_voice NC-280 supervisor fork enforcement

## Problem

`ninchat_voice` NC-280 introduces a public supervisor that routes each Twilio
Media Streams WebSocket through an opaque per-call token:

```text
/voice/{route_token}
```

The application server must not construct Twilio wire XML directly. NC-154 made
`voice_runtime.transports.twilio_call` the transport boundary for stream XML
generation; `ninchat_voice` has a gate that rejects `<Connect>` and `<Stream>`
literals in production code. If the route-token XML builder lives in
`server_fsm.py`, NC-280 violates that boundary even though the runtime behavior is
correct.

## Proposal

Add `build_route_stream_xml(stream_url, route_token)` to
`voice_runtime.transports.twilio_call`.

The helper must:

- convert `https://` to `wss://` and `http://` to `ws://`, matching
  `build_stream_twiml`
- strip a trailing slash from the public base URL before appending `/voice/...`
- percent-encode the route token so opaque tokens remain URL-safe
- leave the existing `/voice` stream helper and alias unchanged

## Acceptance Criteria

- [x] Unit test: HTTPS base URL becomes `wss://.../voice/{token}`
- [x] Unit test: HTTP base URL becomes `ws://.../voice/{token}`
- [x] Unit test: trailing slash on the base URL does not produce `//voice`
- [x] Unit test: route token is percent-encoded
- [x] Existing `build_stream_xml` alias remains unchanged
- [x] No application consumer needs Twilio XML literals to build routed streams

## Out of Scope

- Supervisor worker assignment and token validation - owned by `ninchat_voice` NC-280
- Twilio signature validation - owned by NC-283
- Changing the existing non-routed `/voice` XML contract

## Implementation Notes

This is deliberately a small transport-boundary addition. The supervisor still
owns route-token lifecycle, assignment, stale-token rejection, and WebSocket
proxying. `voice_runtime` only owns the provider-specific XML shape.
