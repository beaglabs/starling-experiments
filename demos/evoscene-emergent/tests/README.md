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

## D2b — explicit backprojection + point cloud

Structural:

~~~sh
zig build test-demo-evoscene-d2b
zig build -Doptimize=ReleaseFast run-demo-evoscene-d2b -- validate
python tools/evoscene_backproject.py --self-test
python tools/verify_evoscene_d2b.py
~~~

Live against the validated D2a artifacts:

~~~sh
python tools/verify_evoscene_d2b.py --live
~~~

## D2c — deterministic fusion + geometry refinement

Structural:

~~~sh
zig build test-demo-evoscene-d2c
zig build -Doptimize=ReleaseFast run-demo-evoscene-d2c -- validate
python tools/evoscene_fuse_refine.py --self-test
python tools/verify_evoscene_d2c.py
~~~

Live against the two validated D2b outputs:

~~~sh
python tools/verify_evoscene_d2c.py --live
~~~

## D2d — deterministic novel-view render + reprojection

Structural:

~~~sh
zig build test-demo-evoscene-d2d
zig build -Doptimize=ReleaseFast run-demo-evoscene-d2d -- validate
python tools/evoscene_render_reproject.py --self-test
python tools/verify_evoscene_d2d.py
~~~

Live closed-loop gate:

~~~sh
python tools/verify_evoscene_d2d.py --live
~~~

## D2e — deterministic projected-surface mesh finalization

Structural:

~~~sh
zig build test-demo-evoscene-d2e
zig build -Doptimize=ReleaseFast run-demo-evoscene-d2e -- validate
python tools/evoscene_finalize_mesh.py --self-test
python tools/verify_evoscene_d2e.py
~~~

Live post-convergence finalizer:

~~~sh
python tools/verify_evoscene_d2e.py --live
~~~

D2e triangulation dependency (already present in the D2a venv):

~~~sh
python -m pip install -r demos/evoscene-emergent/requirements-d2e.txt
~~~
