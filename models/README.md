# Local models

Model weights are local-only and ignored by git.

F4 canonical model:

```text
~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf
```

You may either leave the weights there, create a local link at:

```text
models/gemma-4-E2B-it-Q4_K_M.gguf
```

or set `F4_MODEL_PATH` to the same GGUF at another local path.

Weights are never committed.
