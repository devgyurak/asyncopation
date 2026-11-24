"""Layer 1 - connection ownership.

contextvars-based ownership instead of thread-locals. Default strategy (see ADR-0001):
borrow a pooled connection per query outside transactions, pin one inside a transaction.
"""
