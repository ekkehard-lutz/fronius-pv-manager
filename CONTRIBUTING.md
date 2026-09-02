# Contributing to Fronius PV Manager

Contributions are welcome when they preserve the project's capability-based
architecture and safety guarantees.

## Branch model

- `main` is the stable release line.
- `develop` is the active development and integration branch.
- Feature branches contain focused changes and target `develop`.

External contributors normally work in a fork and do not create branches in
the upstream repository unless they have explicit maintainer permission.

## Contribution workflow

1. Fork the repository.
2. Create a focused feature branch in your fork.
3. Implement the change.
4. Add or update tests and documentation.
5. Run the required local validation.
6. Open a pull request against the upstream `develop` branch.
7. Address review comments by pushing additional commits to the same branch.
8. Test hardware-dependent changes on your hardware where possible.
9. Provide the information maintainers need to review architecture, safety,
   semantics, tests, documentation, and hardware evidence.
10. Merge only after review and validation succeed.

## Development rules

- Write code, code comments, technical documentation, and pull-request
  descriptions in English.
- Preserve the generic capability-based architecture. Avoid product-name logic
  unless actual device behavior makes it technically necessary.
- Never guess register ranges, enum meanings, scale factors, writable semantics,
  or unsupported-device behavior.
- Identify authoritative Fronius or SunSpec documentation for register-semantic
  changes where it is available.
- Clearly label hardware observations as hardware validation. Do not generalize
  them into universal product-family behavior without evidence.
- New writable controls must use the existing fail-closed safety model. Do not
  add arbitrary raw-register write services.
- Preserve semantic validation, exactly-one-write behavior, verified readback,
  and non-optimistic state.
- Write-policy constraints may narrow authoritative semantics but never broaden
  them.
- Behavioral changes require tests.

## Required validation

Run before opening or updating a pull request:

```bash
python -m ruff check .
python -m pytest
git diff --check
```

## Hardware-related changes

State the exact tested hardware model and firmware where relevant. Include
before/after register values or reproducible test steps when useful. If the
maintainers do not own the hardware, contributor testing may be the only
practical hardware validation; support should initially be described as
contributor-tested rather than maintainer-tested.

Do not infer support for nearby products solely from naming similarity. Explain
the discovered SunSpec models and compatible semantics that support the claim.

## Pull requests

Keep pull requests focused and explain:

- the problem being solved;
- affected SunSpec models and registers;
- safety impact for writable functionality;
- automated validation results; and
- hardware validation, when applicable.
