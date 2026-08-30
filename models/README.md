# Local models

Model weights are local-only and ignored by git.

F4 does **not** require the GGUF to be copied into this repository.

Use the existing local file directly:

~~~sh
python3 tools/f4_llama_cpp.py \
  --weights /absolute/path/to/gemma-4-E2B-it-Q4_K_M.gguf \
  --plan smoke \
  --output trials/f4-smoke-raw.tsv
~~~

The live runner SHA-256 hashes the complete GGUF into the F4 metadata sidecar,
so the evidence pins the exact local model bytes regardless of where the file
lives.

Start llama.cpp against the same file:

~~~sh
llama-server \
  -m /absolute/path/to/gemma-4-E2B-it-Q4_K_M.gguf \
  --port 8080 \
  -np 1 \
  -c 4096
~~~

Never commit model weights.

See `docs/F4_HETEROGENEOUS_MODEL_OPERATORS.md` for the smoke and canonical
runbook.
