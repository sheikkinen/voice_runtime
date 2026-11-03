# The fake socket agreed with my reading of the protocol

VR-011 added an AudioHook server transport for the Genesys Audio Connector.
The judged FR listed twenty acceptance criteria. I wrote 35 RED witnesses
against a fake socket that scripted Genesys messages and recorded every send
on a fake clock. GREEN passed all of them, the full suite, ruff, the size
gate and radon. The transport was finished by every gate the repository owns.

The first real client rejected it on every connection. The purecloudlabs
reference client got HTTP 403 on the upgrade. With postponed annotations,
`WebSocket` was imported only under `TYPE_CHECKING`. FastAPI could not
resolve the endpoint's `websocket: WebSocket` annotation, so it never
injected the socket and refused the upgrade before the handler ran. The fake
socket never saw this. The harness captured the handler from the
`app.websocket` decorator and called it directly. That stepped over the one
layer that turns an annotation into a socket.

After the fix, the connection probe was bound as a call. The FR said a probe
has a "null `conversationId`". The tests pushed JSON `null`. The client sends
the nil UUID `00000000-0000-0000-0000-000000000000`, and its own servers
classify a probe by `isNullUuid` on the conversation *or* participant id. My
reading of the protocol, the FR, the judge and the fake socket all agreed,
because they all came from the same reading. The only party with a different
opinion was the protocol's own implementation.

The third finding was about the gate itself. The pinned client treats every
server binary message as fatal (415). It predates bidirectional audio, so the
judged gate "audio both ways" cannot be met with the client as pinned. The
gate went to the owner as an open question, not into the record as a pass.

**Trap:** a hand-written fake of an external peer encodes the author's
reading of the protocol. Tests against it prove that the code agrees with
that reading, not with the peer. Both E-1 defects sat exactly where the fake
was invented: framework injection, which the fake bypassed, and wire values,
which the fake chose.

**Heuristic:** for every transport, keep at least one witness that crosses the
real framework boundary (`TestClient`, a real upgrade). Take protocol
literals such as sentinel ids and probe markers from the reference
implementation's source, not from prose summaries of it. When the evidence
tool cannot express the gate, report the gap; do not reinterpret the gate.

**Seed:** Which other voice_runtime transports are witnessed only through a
captured handler? Would a single real-upgrade smoke test per registered
route, run for every transport, have caught this class of defect before any
field run?
