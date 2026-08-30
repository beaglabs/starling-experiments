# Demo tests

D0 and D1 tests live beside the Zig modules in `../src/` so private contract
helpers can be tested without exporting them as public demo APIs.

Run the D0 deterministic runtime contract gate:

~~~sh
zig build test-demo-evoscene-d0
zig build -Doptimize=ReleaseFast run-demo-evoscene-d0 -- validate
~~~

Run the D1 frozen fixed-reference gate:

~~~sh
zig build test-demo-evoscene-d1
zig build -Doptimize=ReleaseFast run-demo-evoscene-d1 -- validate
~~~

The global repository test step includes both stages:

~~~sh
zig build test
~~~
