"""Versioned local contracts for Pool's native expression extensions.

These contracts are independently tested; they do not claim official BRAIN
parity. Existing TS/CS kernels remain the pinned shared implementation.
"""
CONTRACT_VERSION = "pool-operators-v1"
EXTENSIONS = {
    "sqrt": 1, "log": 1, "power": 2, "signed_power": 2, "sign": 1,
    "is_nan": 1, "if_else": 3,
}
CONTRACT = {
    "version": CONTRACT_VERSION,
    "missing": "NaN is missing; infinity is invalid; neither becomes a numeric zero",
    "is_nan": "Observed NaN is 1, finite is 0, infinity is invalid; unready and absent bars stay invalid",
    "if_else": "Scalar/vector conditions share finite nonzero/zero selection; invalid condition is invalid; only the selected branch is required",
    "sqrt": "Finite x >= 0; negative input is invalid",
    "log": "Natural logarithm, finite x > 0",
    "power": "Real finite result; negative base requires integral exponent; 0**0 is 1; zero to negative power is invalid",
    "signed_power": "sign(x)*abs(x)**y; zero to a nonnegative exponent is zero",
    "zscore": "Pinned shared batch-program population zscore; constant/singleton section is invalid",
    "ts_clock": "Default pinned shared valid-observation clock; explicit clock='trading_days' retains a slot for each complete input session",
    "calendar_windows": "Strict full window, NaN propagates, upstream unready remains unready, absent bars occupy slots but never publish a fabricated observation; std ddof=0/1; linear decay weights 1..D",
    "brain_parity": "not_verified",
}
