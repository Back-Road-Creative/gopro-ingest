# Contributing

Bug reports, patches, and camera-behaviour reports are all welcome.

## Before you open a pull request

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

CI runs exactly that on Python 3.11, 3.12, and 3.13.

## What a good change looks like

- **One thing per pull request.** A bug fix and a refactor in the same diff
  are hard to review and harder to revert.
- **Bug fixes start with a failing test.** Write the test, watch it fail,
  then fix it. That is how we know the test would have caught the bug.
- **No test is deleted or weakened to make a suite pass.**
- **Behaviour changes update the README in the same commit.** The defaults
  table and the "Honest limits" section are part of the contract, not
  decoration.

## Fixtures

**Never commit real GPS coordinates, real tracks, or real footage.** Every
coordinate in the test suite is synthetic and derived from a round-number
anchor in `tests/conftest.py`. A test fixture is a permanent public record of
wherever it points, so please do not "improve" one by substituting a real
route or a recognisable place. If you need a new location fixture, derive it
from the existing anchors.

The same goes for issue reports: strip coordinates out of any GPX or ffprobe
output before pasting it. A filename and a description of the shape of the
data are almost always enough.

## Thresholds

If you find yourself hard-coding a duration, distance, or tolerance, it
probably belongs in `IngestConfig` or `CorrespondenceTolerances` with a
documented default. The point of this library is that its policy is yours,
not the author's.

## Reporting camera behaviour

Reports about what a particular camera model actually writes are especially
useful — chapter naming, telemetry availability, container tags, and the
various ways a receiver misbehaves at cold start. Please include the model,
the firmware version if you have it, and `ffprobe -show_streams` output with
any location tag removed.

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
