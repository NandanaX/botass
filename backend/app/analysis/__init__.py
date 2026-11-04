"""Technical chart analysis engine (Phase 4).

Every analyzer is a pure, causal function of closed candles: a result reported for bar
``i`` uses only bars ``<= i`` (pivots only from their confirmation bar), so evaluating
on truncated history gives identical results (no look-ahead, no repainting).
"""
