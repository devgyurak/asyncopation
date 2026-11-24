from collections.abc import AsyncIterator

import pytest

from asyncopation.testing import RecordingDriver


@pytest.fixture
async def driver() -> AsyncIterator[RecordingDriver]:
    """An opened recording driver (connections and capabilities need `open()`)."""
    recording = RecordingDriver()
    await recording.open()
    yield recording
    await recording.close()
