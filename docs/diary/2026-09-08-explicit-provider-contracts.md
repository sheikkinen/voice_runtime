# Explicit inputs must survive the SDK boundary

VR-004 and VR-006 were amended and independently judged before implementation.
Both second-round verdicts are APPROVED; the documentation PR records those
decisions, not completed runtime behavior.

Two tempting shortcuts failed a cheap probe. Setting an SDK API base URL did
not make routing explicit: ambient region/edge settings still changed the
final request host. Importing Pydantic successfully did not prove the package
declared it: FastAPI supplied it transitively. The public typed-model plan
therefore required a direct dependency declaration, explicitly re-judged.

**Trap:** confusing an available capability with a declared contract.

**Heuristic:** inspect the request at the transport boundary and the dependency
at the distribution boundary. Constructor arguments and a working developer
environment are intermediate observations, not proof of either contract.

The judges also separated shared release coordination from implementation
authority. Two features can ship together without either acceptance suite
requiring the other's unimplemented exports.

**Seed:** Which other supposedly explicit SDK settings can ambient process
configuration override after our boundary has validated them?