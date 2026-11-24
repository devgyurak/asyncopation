"""Layer 8 - public test utilities (async query capture, ``assert_num_queries``).

``RecordingDriver`` is an ``AsyncDriver`` with queued results and a statement log, for unit tests
of upper layers.
"""

from asyncopation.drivers.base import Capabilities, ExecResult
from asyncopation.testing.recording import (
    NoQueuedResult,
    RecordingConnection,
    RecordingDriver,
    Statement,
)

__all__ = [
    "Capabilities",
    "ExecResult",
    "NoQueuedResult",
    "RecordingConnection",
    "RecordingDriver",
    "Statement",
]
