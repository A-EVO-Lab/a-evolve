Tasks are binary prediction market questions (YES/NO outcomes).
The solver decides BUY YES, BUY NO, or SKIP based on reasoning.

Focus on STRATEGY gaps — what's the solver doing wrong?
- Trading at bad prices (buying YES at 0.90 when true prob is 0.85)
- Overconfidence (predicting 0.95 when calibrated accuracy is 0.70)
- Missing easy markets (skipping obvious YES/NO with clear evidence)
- Wrong reasoning patterns (anchoring on irrelevant information)

GOOD gap names: overconfidence_calibration, sports_base_rate_error,
  political_market_skip_rate, price_sensitivity_threshold
BAD gap names: wrong_answer, low_accuracy, bad_prediction

Analyze trajectories to find systematic errors in the solver's
decision-making process, not just individual wrong answers.
