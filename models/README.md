# Local models

Model weights are local-only and ignored by git.

F4 canonical target:

~~~text
~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf
~~~

The live runner defaults to that path and SHA-256 hashes the complete GGUF
into the F4 metadata sidecar before evidence is accepted.

An optional local symlink keeps the run command shorter:

~~~sh
ln -s ~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf \
  models/gemma-4-E2B-it-Q4_K_M.gguf
~~~

Weights and symlinks under this directory remain ignored. Never commit model
weights.

Start the canonical llama.cpp server with:

~~~sh
llama-server \
  -m ~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf \
  --port 8080 \
  -np 1 \
  -c 4096
~~~

See docs/F4_HETEROGENEOUS_MODEL_OPERATORS.md for the smoke and canonical
runbook.
