# VR-013 AC-12 live run 1: evidence and retrospective rescore

Sanitised record for [VR-013](../VR-013-google-chirp-speech-providers.md) AC-12, required by round-3 revision R-2. Synthetic Finnish text only; no credentials, no caller data. The home directory is shown as `~`.

## 1. Original run (2026-11-16)

- **Command:** `.venv/bin/python scripts/probe_vr013_google_session.py --project scp-tenant-csap-test > tmp/vr013-live.log 2>&1`
- **Code:** `3152539` (probe and providers). Python 3.14.7. `voice-runtime 0.1.12` is stale editable-install metadata; the code was imported from the checkout.
- **SDKs:** google-cloud-speech 2.41.0, google-cloud-texttospeech 2.38.0, google-api-core 2.44.0.
- **Exit code:** 1. **Original result: FAIL** under the original exact-sequence scorer. This outcome is not rewritten.

Complete original output (`tmp/vr013-live.log`, 16 lines, verbatim apart from the home path):

```text
command: ~/src/yamlgraph/projects/voice_runtime/.venv/bin/python scripts/probe_vr013_google_session.py --project scp-tenant-csap-test
git: 3152539fc44dc4235a6e4a4d42ea93697b6a7749 python: 3.14.7
voice-runtime 0.1.12
google-cloud-speech 2.41.0
google-cloud-texttospeech 2.38.0
google-api-core 2.44.0
tts: phrases=98 synth_s=26.7 ttfc_ms first=407 median=165 max=407
feed_start_s=0.77
stream_open_s=1.03,274.42
rotate at_s=274.42 since_first_open_s=273.39: Google STT stream rotate age_s=273 forced=false drained=true
expected (98): talo kissa koira metsä järvi kukka kirja lintu omena leipä maito auto vene juna silta kivi ranta meri saari vuori pilvi sade lumi tuuli aurinko tähti taivas kello tuoli pöytä ikkuna katto lattia lamppu sänky peili kuppi lusikka haarukka veitsi lautanen kattila uuni sauna koivu mänty vaahtera mustikka mansikka puolukka porkkana peruna sipuli tomaatti kurkku banaani appelsiini sitruuna kahvi mehu juusto kala lohi hauki ahven karhu susi kettu jänis hirvi orava siili pöllö varis joutsen sorsa lokki perhonen muurahainen mehiläinen hevonen lehmä lammas possu kana kukko tiikeri leijona norsu kirahvi apina delfiini valas kilpikonna sammakko käärme pallo nukke
committed (101): talo kissa koira kohtametsä kohtajärvi kukka kohtakirja kohtalintu omena leipä maito auto vene juna silta kohtakivi kohtaranta meri kohtasaari kohtavuori pilvi ohtafari lumi tuuli aurinko kohtatähti taivas kello tuoli pöytä ikkuna katto kohtalattia kohtalamppu sänky peili kuppi kohtalusikka haarukka veitsi lautanen kattila kohpa uuni sauna koivu kohtamänty kohtavaahtera mustikka mansikka kohtapuolukka porkkana peruna sipuli tomaatti kurkku banaani appelsiini sitruuna kahvi kohtamehu kohtajuusto kala lohi hauki ahven karhu susi kettu jänis hirvi seuraava fiili pöllö kohtalais joutsen sorsa kohtalokki perhonen muurahainen mehiläinen hevonen kohtalehmä kohtalammas possu kana kukko tiikeri leijona kohtan orsu kirahvi apina corpo dela filme valas kilpikonnat sammakko pultti käärme
missing: ['delfiini', 'juusto', 'järvi', 'kilpikonna', 'kirja', 'kivi', 'lammas', 'lamppu', 'lattia', 'lehmä', 'lintu', 'lokki', 'lusikka', 'mehu', 'metsä', 'mänty', 'norsu', 'nukke', 'orava', 'pallo', 'puolukka', 'ranta', 'saari', 'sade', 'siili', 'tähti', 'vaahtera', 'varis', 'vuori']
duplicated_or_unexpected: ['corpo', 'dela', 'fiili', 'filme', 'kilpikonnat', 'kohpa', 'kohtajuusto', 'kohtajärvi', 'kohtakirja', 'kohtakivi', 'kohtalais', 'kohtalammas', 'kohtalamppu', 'kohtalattia', 'kohtalehmä', 'kohtalintu', 'kohtalokki', 'kohtalusikka', 'kohtamehu', 'kohtametsä', 'kohtamänty', 'kohtan', 'kohtapuolukka', 'kohtaranta', 'kohtasaari', 'kohtatähti', 'kohtavaahtera', 'kohtavuori', 'ohtafari', 'orsu', 'pultti', 'seuraava']
errors: []
RESULT FAIL: committed words differ from expected sequence
```

### What the log does and does not contain

- **Complete:** the ordered list of 98 target words (`expected`), stream opens (1.03 s, 274.42 s), the single rotation record (`age_s=273 forced=false drained=true`), errors (`[]`).
- **Processed, not raw:** the `committed` line is the output of the original probe's `committed_words()`: each final transcript lower-cased, split on non-letters, and every **standalone** `kohta` token dropped. Joined forms (`kohtametsä`) are kept. Raw transcript texts, punctuation and the boundaries between final transcripts are not recoverable.
  - Effect on scoring: none on target letters. Under the whitespace- and punctuation-insensitive rule, the only lost content is standalone carrier words, which are not scored. The rescore below therefore treats each missing standalone carrier as an unmatched carrier.
  - This is a known limit of the historical evidence. Using it for AC-12 needs the owner's explicit acceptance (see §3).
- **Not recorded:** per-phrase feed times and per-commit receipt times. The ±5 s rotation-boundary timing view is **unavailable** for run 1 and is not inferred from word order.

## 2. Retrospective rescore under A-2 (offline, no network)

- **Command:** `.venv/bin/python scripts/probe_vr013_google_session.py --rescore tmp/vr013-live.log`
- **Scorer:** `score()` / `align()` in `scripts/probe_vr013_google_session.py` at `1b6fe8b`: casefold, keep `str.isalnum()` characters, reference `Kohta <target>` per phrase, maximum-weight two-state character alignment (target character 2, carrier character 1, +1 per continued run of matches, deterministic trace-back). A target matches when every one of its characters is aligned. Loss signal: 3 or more consecutive unmatched targets.
- **Exit code:** 0.

Complete rescore output (per-word flags, totals):

```text
scorer: align(): max-weight char alignment on casefolded isalnum chars (target 2, carrier 1, +1 per continued run; deterministic trace-back); carrier='Kohta'; loss_run=3
  ok   talo
  ok   kissa
  ok   koira
  ok   metsä
  ok   järvi
  ok   kukka
  ok   kirja
  ok   lintu
  ok   omena
  ok   leipä
  ok   maito
  ok   auto
  ok   vene
  ok   juna
  ok   silta
  ok   kivi
  ok   ranta
  ok   meri
  ok   saari
  ok   vuori
  ok   pilvi
  MISS sade
  ok   lumi
  ok   tuuli
  ok   aurinko
  ok   tähti
  ok   taivas
  ok   kello
  ok   tuoli
  ok   pöytä
  ok   ikkuna
  ok   katto
  ok   lattia
  ok   lamppu
  ok   sänky
  ok   peili
  ok   kuppi
  ok   lusikka
  ok   haarukka
  ok   veitsi
  ok   lautanen
  ok   kattila
  ok   uuni
  ok   sauna
  ok   koivu
  ok   mänty
  ok   vaahtera
  ok   mustikka
  ok   mansikka
  ok   puolukka
  ok   porkkana
  ok   peruna
  ok   sipuli
  ok   tomaatti
  ok   kurkku
  ok   banaani
  ok   appelsiini
  ok   sitruuna
  ok   kahvi
  ok   mehu
  ok   juusto
  ok   kala
  ok   lohi
  ok   hauki
  ok   ahven
  ok   karhu
  ok   susi
  ok   kettu
  ok   jänis
  ok   hirvi
  MISS orava
  MISS siili
  ok   pöllö
  MISS varis
  ok   joutsen
  ok   sorsa
  ok   lokki
  ok   perhonen
  ok   muurahainen
  ok   mehiläinen
  ok   hevonen
  ok   lehmä
  ok   lammas
  ok   possu
  ok   kana
  ok   kukko
  ok   tiikeri
  ok   leijona
  ok   norsu
  ok   kirahvi
  ok   apina
  MISS delfiini
  ok   valas
  ok   kilpikonna
  ok   sammakko
  ok   käärme
  MISS pallo
  MISS nukke
matched 91/98 (92.9%)
unmatched: ['sade', 'orava', 'siili', 'varis', 'delfiini', 'pallo', 'nukke']
inserted: ['f', 'ri', 'p', 'seu', 'a', 'f', 'l', 'corp', 'a', 'lme', 'pult', 'i']
duplicates: ['talo', 'kana']
longest_unmatched_run: 2
boundary_timing: unavailable (no phrase or commit timestamps recorded)
RESCORE PASS (word criterion only)
```

Reading:

- `inserted` lists hypothesis characters outside the alignment, at character level (for example `corp`, `lme` from `corpo dela filme`, `pult` from `pultti`). `duplicates` are substring hits (`talo`, `kana` also occur inside other recognised words); both are report-only.
- Unmatched: `sade`, `orava`, `siili`, `varis`, `delfiini` (misrecognitions) and `pallo`, `nukke` (the last two phrases). The cause of the final two is **unconfirmed**; no re-run is planned.
- The rotation-boundary phrase `norsu` (`kohtan` + `orsu`) is matched: it reconstructs after whitespace normalisation. Whether such a split is harmless downstream is not shown here.

## 3. AC-12 criteria from this evidence

| Criterion (§4) | Run 1 |
|---|---|
| No 403/404/region error, no provider error | `errors: []`, no WARNING lines |
| No timeout | the run completed; no timeout line |
| One ordinary rotation at stream age ≥ 270 s | one rotation, `forced=false`, stream opened at 1.03 s, rotated at 274.42 s: age 273.39 s |
| No forced rotation | none |
| No run of ≥ 3 consecutive unmatched targets | longest run 2 |

**Retrospective PASS under A-2** (scorer above at `1b6fe8b`), subject to the owner accepting the processed `committed` line as the historical sequence (§1). The original FAIL, exit 1, stands.

Limits of the 3-word rule: it detects a pattern, not its cause. Lost audio can remove one or two words, and misrecognition can spoil three or more. It does not gate inserted text, duplicates or overall quality.
