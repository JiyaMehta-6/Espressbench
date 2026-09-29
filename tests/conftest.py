import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from espbench.simulation import EchoServer


@pytest.fixture
def echo_server():
    server = EchoServer().start()
    yield server
    server.stop()
