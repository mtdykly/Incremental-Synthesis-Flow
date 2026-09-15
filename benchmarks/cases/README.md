# Case interpretation

- `eco-001`: Base and New both select `d665812`. This is a no-change control,
  not evidence of functional ECO support.
- `eco-002`: `afa9cbb` to `71cbf1d` changes the control-transfer implementation.
  No Base/New equivalence assumption is made. The required proof compares the
  incremental implementation against **New**, with unchanged register semantics.

The original checkout recorded gitlinks without a source repository URL.
Provide the actual RTL repository URL to `scripts/benchmark/fetch_source.sh`;
it checks that all required commits exist. Then run `checkout_case.sh`.
Temporary worktrees must not be committed. Existing `equiv.log` files are
historical artifacts, not evidence of a current successful proof.
