import importlib.util
from pathlib import Path

import pytest

AGENT_PATH = Path(__file__).resolve().parents[1] / "firmware" / "agent.py"


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("espbench_agent", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""

    def recv(self, size):
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def send(self, data):
        self.sent += data
        return len(data)

    def close(self):
        pass


def test_segmented_post_body_is_reassembled(agent):
    headers = (b"POST /echo HTTP/1.1\r\nHost: device\r\n"
               b"Content-Length: 8192\r\n\r\n")
    payload = b"A" * 8192
    client = FakeClient([headers, payload])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 200 OK")
    assert client.sent.endswith(payload)


def test_body_split_across_three_chunks(agent):
    headers = b"POST /echo HTTP/1.1\r\nContent-Length: 6\r\n\r\n"
    client = FakeClient([headers, b"ab", b"cd", b"ef"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 200 OK")
    assert client.sent.endswith(b"abcdef")


def test_single_chunk_get_stats(agent):
    client = FakeClient([b"GET /stats HTTP/1.1\r\nHost: x\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 200 OK")
    assert b"X-Boot-Count:" in client.sent
    assert b'"agent"' in client.sent


def test_mark_decodes_percent20_labels(agent):
    client = FakeClient([b"GET /mark?label=hello%20world HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 200 OK")
    assert b'"hello world"' in client.sent


def test_query_decodes_plus_and_percent20(agent):
    params, path = agent._query("/mark?label=a+b%20c")
    assert path == "/mark"
    assert params == {"label": "a b c"}


def test_unknown_path_returns_404(agent):
    client = FakeClient([b"GET /nope HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 404 Not Found")


def test_empty_request_returns_400(agent):
    client = FakeClient([b"", b""])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 400 Bad Request")


def test_sensor_and_clock_endpoints(agent):
    client = FakeClient([b"GET /sensor?mode=hang HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert b'"hang"' in client.sent
    client = FakeClient([b"GET /clock?offset=5000 HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert b'"offset": 5000' in client.sent
    assert agent.SENSOR_MODE["value"] == "hang"
    assert agent.CLOCK_OFFSET["value"] == 5000
    agent.SENSOR_MODE["value"] = "ok"
    agent.CLOCK_OFFSET["value"] = 0


def test_bad_clock_offset_returns_400(agent):
    client = FakeClient([b"GET /clock?offset=abc HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 400 Bad Request")
    assert agent.CLOCK_OFFSET["value"] == 0


def test_bad_content_length_returns_400(agent):
    client = FakeClient([b"POST /echo HTTP/1.1\r\nContent-Length: abc\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 400 Bad Request")


def test_non_utf8_header_does_not_500(agent):
    client = FakeClient([b"GET /stats HTTP/1.1\r\nX-Junk: \xff\xfe\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 200 OK")


def test_garbage_request_line_returns_400(agent):
    client = FakeClient([b"\xff\xff\xff\xff"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 400 Bad Request")


def test_agent_version_matches_package(agent):
    import espbench

    client = FakeClient([b"GET /stats HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert f"espbench-agent/{espbench.__version__}".encode() in client.sent


def test_stats_includes_fw(agent):
    client = FakeClient([b"GET /stats HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert f'"fw": "{agent.FW_VERSION}"'.encode() in client.sent


def test_version_endpoint(agent):
    client = FakeClient([b"GET /version HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 200 OK")
    assert f'"{agent.FW_VERSION}"'.encode() in client.sent


def test_log_endpoint_records_mark_events(agent):
    agent.LOG.clear()
    client = FakeClient([b"GET /mark?label=door HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    client = FakeClient([b"GET /log HTTP/1.1\r\n\r\n"])
    agent.handle(client)
    assert client.sent.startswith(b"HTTP/1.1 200 OK")
    assert b'"fw"' in client.sent
    assert b"MARK door" in client.sent
    agent.LOG.clear()


def test_log_ring_buffer_is_capped(agent):
    agent.LOG.clear()
    for index in range(agent.LOG_MAX + 40):
        agent._log("m" + str(index))
    assert len(agent.LOG) == agent.LOG_MAX
    assert agent.LOG[-1] == "m" + str(agent.LOG_MAX + 39)
    agent.LOG.clear()
