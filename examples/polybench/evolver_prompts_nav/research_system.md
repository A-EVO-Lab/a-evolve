PolyBench-specific context for the researcher — full system (with branching).

This run uses git branches; your research records drive whether the
builder commits a fix to main or to a specialized branch. For each
candidate approach you record, indicate transferability:

For each approach you test, document:
- TRANSFERABILITY: which PolyBench event types or domains besides
  "{regime}" would this approach help, hurt, or be neutral on?
  Test cross-event-type if the approach risks becoming
  type-specific (e.g., a binary-yes/no decision rule applied to
  multi-outcome markets).
- Each research record SHOULD include:
    transferable: <true|false>
    helps_categories: [list of event types or domains]
    hurts_categories: [list]
    recommended_target: <main | branch/{regime}>
  Decision rules / heuristics that help multiple event types without
  harm go to main; rules tuned to one type go to branch/{regime}.
