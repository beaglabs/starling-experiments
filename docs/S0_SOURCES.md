# S0 frozen-source manifest

The experiment substrate is re-materialized without semantic edits from
`beaglabs/starlings` at the parent of commit `176a0f9`:

```text
historical source commit:
e943ea84a71bbf0189a54d0a703052b1515629cf
```

| Local path | Historical Git blob |
| --- | --- |
| `src/substrate/stage5/stage5a_scaling.zig` | `0b45d61506611b9b2d370391a6eafe5b5de0569b` |
| `src/substrate/stage5/stage5a_cli.zig` | `3dc58e065f222a3ebb8efcff1357a44bd557d2cc` |
| `src/substrate/stage7/stage7a_policy.zig` | `3efd8d3702d3918e91eab3ae5500f155364a5b34` |
| `src/substrate/stage7/stage7a_cli.zig` | `f95ce6aaa81fcae09524fb6527733ea4b6c682e3` |
| `src/substrate/stage7/stage7c_async_transfer.zig` | `d232fadb975c222daab26eb879ae1546fb44bb54` |
| `src/substrate/stage7/stage7c_cli.zig` | `69b9a1211ffc08fbb27823dc816bf2a65718585a` |

`tools/verify_s0.py` recomputes the Git blob identity before executing any
scientific validation.

Canonical Stage 7C first-suite SHA-256:

```text
c89d1985af0479191126fca91265b1fe7f49e7b34db471e13c74e8bb28195a36
```
