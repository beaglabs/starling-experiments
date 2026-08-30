# D0 tests

D0 tests live beside the Zig modules in `../src/` so private contract helpers
can be tested without exporting them as public demo APIs.

Run only the D0 test root:

~~~sh
zig build test-demo-evoscene-d0
~~~

Run the deterministic replay/accounting validator:

~~~sh
zig build -Doptimize=ReleaseFast run-demo-evoscene-d0 -- validate
~~~

The global repository test step also includes D0:

~~~sh
zig build test
~~~
