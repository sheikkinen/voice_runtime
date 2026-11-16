# The scorer failed the provider

VR-013 added Google Chirp 3 STT and TTS. The live probe fed 98 synthetic
phrases, each `"Kohta <word>."`, through one five-minute session that crossed
an ordinary stream rotation. It returned FAIL. Read with care, the failure
belonged to the probe, not to Chirp. About twenty "missing" words were Chirp
writing the spoken carrier joined to the target (`kohtametsä`). The probe
dropped only a standalone `kohta`, so each joined word counted as both
missing and unexpected. The owner read the log in a few minutes and named it
a punctuation issue: the next consumer is an LLM, which does not care about
whitespace.

The strict rule had been judged, frozen and enforced. Nobody had asked what
the transcript is *for*. The criterion measured agreement with a word list,
while the business needs content an LLM can use. Amendment A-2 changed the
scorer to compare without regard to whitespace, and the round-3 judge then
asked for the scorer to be specified and proved with tests.

The second trap appeared inside the fix. The FR named
`difflib.SequenceMatcher`, a reasonable choice for "align two strings". The
first synthetic test failed. The greedy longest-block step gave a target's
leading letters to the neighbouring carrier `kohta` and left the target
unmatched. A plain weighted LCS (longest common subsequence) fixed that, but
ties then split `metsä` across two places. Only a bonus for continuing a run
made the alignment both correct and deterministic. Each wrong aligner gave
output that looked like an answer. The witness tests were the only thing
that could tell them apart.

The third finding was about process. voice_runtime has no installed
pre-commit hooks. The first GREEN commit landed over the size and complexity
gates without anyone noticing. Since then, every commit in this FR has run
the gates by hand. A gate that a fresh clone does not install is only a
suggestion.

**Trap:** a test oracle is specified from the provider's output format
instead of the consumer's need. It then fails a correct provider for a
reason the consumer would never notice. A plausible library choice inside
the oracle (greedy alignment) can hide a second false verdict.

**Heuristic:** before freezing a live acceptance rule, state in one sentence
what the downstream consumer does with the output. Score only what that
sentence depends on. Give any scorer its own synthetic witnesses before it
judges real data, including the tie and repetition cases.

**Seed:** if the consumer is an LLM, should the acceptance oracle itself be
"the extraction graph produces the same record from the transcript as from
the reference text", turning provider evaluation into a CSAP end-to-end
property instead of a string metric?
