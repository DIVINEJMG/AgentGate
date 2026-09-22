# F26 shadow mode and cutover

## Shadow mode

Shadow mode is limited to deterministic/read-only comparisons. The Python candidate and preserved
TypeScript reference may receive equivalent inputs for parity comparison, but external side effects
must never be duplicated.

`ShadowModeService` refuses `side_effect=True`. TypeScript reference code under `reference/`
remains non-authoritative and is not deployed.

## Progressive cutover order

Authority progresses only in this order:

1. frontend
2. system / health
3. authentication
4. organizations
5. workforce
6. jobs
7. results
8. governance
9. scheduler
10. runtime
11. integrations / side effects

`CUTOVER_STAGE` records the current highest authoritative stage. Runtime and scheduler processes
enforce the applicable stage before doing work.

The dangerous surfaces move last. A later stage may not be treated as authoritative merely because
its schema or interface exists.
