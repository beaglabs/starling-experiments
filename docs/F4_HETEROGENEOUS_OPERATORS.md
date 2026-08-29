# F4 — neutral heterogeneous AI operators

F4 is the live-model exercise of the operator-neutral Starlings architecture.

The historical Stage 3F.0 llama.cpp runner is preserved byte-for-byte at:

\`\`\`text
tools/historical/stage3f0_llama_cpp.py
source blob: f84098b579fda8c472eac0f16e9e05e3d534bf16
source commit: 176a0f95b3d2c668b3d8cb6b3231d5d59609999e
\`\`\`

The active F4 runner is a new adapter. Historical code is not edited in place.

## Model

Canonical local model:

\`\`\`text
~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf
\`\`\`

\`F4_MODEL_PATH\` may point to the same weights at another local path.

The verifier starts Homebrew \`llama-server\` on localhost only unless an
already-running local server is supplied through \`F4_BASE_URL\`.

## Measurement boundary

Python owns only:

- prompt construction;
- llama.cpp HTTP requests;
- sampling seed;
- raw completion capture;
- metadata sidecar generation.

Zig owns the canonical measurement:

- expected operator class per worker;
- environment/sampling seed validation;
- cached knowledge replay;
- protocol parsing;
- invalid output rejection;
- semantic validation;
- ring/grid topology;
- fact merge;
- success and round budgets;
- communication/useful/duplicate accounting;
- deterministic trajectory hashes.

The same raw evidence must replay byte-identically.

## Population arms

Five workers and five overlapping facts are used in all arms.

\`\`\`text
deterministic_only:
  workers 1-5 deterministic

mixed:
  workers 1 and 3 Gemma
  workers 2, 4, 5 deterministic

model_only:
  workers 1-5 Gemma
\`\`\`

The deterministic operator emits a protocol-valid claim of its current
knowledge. It is deliberately simple and exists as a neutral runtime control,
not as an optimized Stage 7 policy.

## Experimental axes

\`\`\`text
population:       deterministic_only, mixed, model_only
topology:         ring, grid
environment seed: 0, 1
sampling seed:    0, 1
decoding arm:     typed_unconstrained, cfg_constrained
round budget:     5
\`\`\`

This yields 48 population runs.

The CFG is the re-materialized Stage 3F.0 interaction grammar:

\`\`\`text
CLAIM <fact-list>
QUERY EVIDENCE <fact>
\`\`\`

Invalid model output is never repaired. Syntax-invalid output is counted as
\`invalid_actions\`; protocol-valid but semantically invalid output is rejected
and counted as \`semantic_violations\`.

## Evidence

Run:

\`\`\`sh
python3 tools/verify_f4.py
\`\`\`

The gate:

1. runs the F4 runner self-test and dry-run;
2. runs all Zig tests;
3. launches/uses local llama.cpp with the Gemma GGUF;
4. generates \`trials/f4-raw.tsv\` plus metadata;
5. replays the raw evidence twice through Zig and requires byte identity;
6. freezes \`trials/f4-heterogeneous.tsv\`;
7. reports success, model calls, invalid/semantic rejection, communication,
   useful/duplicate delivery, budget compliance, and trajectory multiplicity.

Structural replay/budget/operator-class failures are hard failures.

Live model backend errors, zero mixed-population convergence, or zero valid
model-only protocol actions produce a completed \`LIMITATION\` result rather
than changing the experiment after observation.
