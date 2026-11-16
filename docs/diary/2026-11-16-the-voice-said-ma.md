# The voice said "mä"

VR-014 added `python -m voice_runtime.speech_check`, a tool that synthesises a
sentence, recognises it again and scores the result. Offline, 78 tests went
from RED to GREEN on the first run. Two findings came from running the real
thing.

The first came from the tool's own header. It said `version=0.1.12` for a
checkout at 0.5.0. The code was imported from the checkout, but
`importlib.metadata` answered from a stale editable install's
`dist-info`. Every unit test passed, because no test had asked which source
the version should come from. The value looked right and would have been
pasted into incident notes as fact. The fix normalises at the boundary: inside
a voice-runtime checkout, the version and the SHA both come from that
checkout; metadata is trusted only in a wheel install. A RED test came first.

The second came from the live run. Five of six provider pairs passed with
WER 0. ElevenLabs into ElevenLabs failed at 0.25: "*Mä* haluaisin … *ens*
viikolla". The reflex was to blame the recogniser, or to re-run until it
passed. Instead the saved audio went to Azure STT, which heard the same
colloquial words. The voice had said them. In the next run, the same voice
read the sentence correctly. One cheap cross-check moved the blame from the
recogniser to the synthesiser, and the owner could accept the FAIL as a
voice finding instead of a tool defect.

Re-running a live check until it passes is cherry-picking. It turns a tool
that measures variance into one that hides it.

**Trap:** a roundtrip failure is blamed on the step that reports it (the
recogniser), when the defect entered one step earlier. A metadata lookup is
trusted because it is the standard API, although the environment behind it is
stale.

**Heuristic:** when a pipeline fails at the end, swap one stage for an
independent one before blaming any. Here, the same audio went to a second
recogniser. For self-reporting fields (version, SHA), name the source of truth
for each environment, and test the environment where the default source lies.

**Seed:** should the speech check carry its own cross-check: on a roundtrip
FAIL, automatically recognise the same audio with a second available provider,
so the row says whether the synthesiser or the recogniser diverged?
