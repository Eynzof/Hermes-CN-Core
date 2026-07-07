"""Performance test suite for Hermes-CN-Core.

All tests in this package run in OFFLINE mode — no real LLM API calls.
Every network-bound model call is replaced with mocks/fakes/canned responses.
This ensures deterministic, cost-free, CI-friendly results.
"""