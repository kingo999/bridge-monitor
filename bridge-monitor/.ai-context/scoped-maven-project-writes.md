# Scoped Maven project writes

Approved 2026-11-05 for the MRI `hardware-report-2` implementation. The existing Bridge leases remain the authority for reads, writes and commands.

- `pom_write_paths` grants exact POM paths on an exact registered repository. A write still needs a matching task scope and expected hash for existing files. Package manifests, lockfiles and `.mvn` remain protected.
- `sql_write_prefixes` supports explicitly registered nested roots. SQL writes still require the ordinary host and task write grants. SQL files execute through disposable database integration tests via the existing Maven runner.
- `write_denied_prefixes` prevents writes through both the registered project and an enclosing root while retaining read access. The MRI policy protects `hardware-report/` and targets `hardware-report-2` with Maven.

The focused repository grant, execution and worktree suites returned 86 passes and one symlink privilege skip. The current runtime was loaded through the existing desktop launcher. Its live task grant was issued for call `389fa737-42d5-5dff-a01d-9e77c3473545`.

[Repository execution guidance](../docs/operations/repository-execution.md)
