"""The context engine: everything a model is shown (docs/plans/context-quality-fix-plan.md, 3).

- `evidence`: what the planner sees of a provider's output: shape-aware, noise-free and bounded,
  with every cut marked.
- `narrator`: the plain sentence that says what a step did.
- `sections`: prompts as named sections, measured per call.
"""
