# Contributing

Use Python 3.11+ and Bash on a POSIX system. From the repository root, run:

```bash
bash scripts/test.sh
```

The suite runs offline and includes shell checks. Keep changes focused, add
relevant tests for behavior changes, and keep documentation consistent with the
implemented protocol. Never include credentials or personal data in fixtures,
logs, or examples.

This repository accepts only provider-neutral, general-purpose adapters.
Adapters for specific vendors or services should live in separate repositories.
See the [adapter guide](docs/ADAPTERS.md) for integration and validation steps.
