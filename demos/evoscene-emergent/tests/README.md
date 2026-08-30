# Demo tests

D0, D1, and D2a Zig tests live beside the implementation modules in `../src/`.

## D0 — deterministic runtime contracts

~~~sh
zig build test-demo-evoscene-d0
zig build -Doptimize=ReleaseFast run-demo-evoscene-d0 -- validate
~~~

## D1 — frozen fixed reference

~~~sh
zig build test-demo-evoscene-d1
zig build -Doptimize=ReleaseFast run-demo-evoscene-d1 -- validate
~~~

## D2a — real spatial-prior contract

Structural:

~~~sh
zig build test-demo-evoscene-d2a
zig build -Doptimize=ReleaseFast run-demo-evoscene-d2a -- validate
python3 tools/evoscene_moge_prior.py --self-test
python3 tools/verify_evoscene_d2a.py
~~~

Live model gate after installing `../requirements-d2a.txt`:

~~~sh
python3 tools/verify_evoscene_d2a.py --live
~~~

## Repository gate

~~~sh
zig build test
~~~
