# Two recorded causes were one region

CSAP NC-488 recorded two independent reasons why the media-teardown REST
hangup could never succeed. The worker's auth token was rejected by
`api.twilio.com` (401/20003), and the inbound call legs lived in IE1, not
US1. The incident was then handled as two problems: a credential task
(rotate the token) and a routing gap (VR-007).

A read-only probe took four requests: each credential against each regional
host, on the account resource and on a fabricated call SID. The "rejected"
token answered 200 on `api.dublin.ie1.twilio.com`. Twilio auth tokens are
region-scoped. The token was never bad; ambient mode sent a valid IE1 token
to US1. The rotation task would have spent an owner credential change on a
non-defect and left the 31921 fallback in place.

The same probe settled the FR's riskiest open premise before the judge saw
it. The US1 API key classifies a foreign-region call as 404/20404, not 401,
so the ordered sweep advances instead of stopping at the first host. One
live call to the operator's phone, ended by `hangup_call(regions=...)` in
0.76 s, exercised the exact library function and consumer tuple the FR wires
together. The evidence reached the judgement as data, not as an assumption.

The probe also showed its own limits. It ran from a local `.env` refreshed
that morning, not from the deployed TEST secret. The first amended FR said
"falsified" where the evidence supported "contradicted by the local token".
The wording had to be narrowed before judgement. A probe answers for the
credentials it holds, not for those with the same name in another
environment.

**Trap:** counting the symptoms an incident record lists as independent
causes because they were written as separate bullets.

**Heuristic:** when two recorded failure reasons share a boundary (here: one
credential, one region axis), probe the cross product (each credential ×
each host) before planning separate cures. A 2×2 matrix is four requests;
two parallel fixes are two FRs.

**Seed:** Which other "rejected credential" incidents in the consumer
repositories were regional misroutes of a valid secret, and would a
region-aware probe at startup have named them on the first failure?
