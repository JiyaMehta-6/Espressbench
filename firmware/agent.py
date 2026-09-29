import json
import time

try:
    import usocket as socket
except ImportError:
    import socket

import gc

BOOT_FILE = "boot_count"
SENSOR_MODE = {"value": "ok"}
CLOCK_OFFSET = {"value": 0}
FW_VERSION = "0.1.0"
LOG = []
LOG_MAX = 64


def _log(message):
    LOG.append(str(message))
    while len(LOG) > LOG_MAX:
        LOG.pop(0)


def _read_boot_count():
    try:
        with open(BOOT_FILE) as handle:
            return int(handle.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _bump_boot_count():
    count = _read_boot_count() + 1
    with open(BOOT_FILE, "w") as handle:
        handle.write(str(count))
    return count


def connect(ssid, password):
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        deadline = time.time() + 20
        while not wlan.isconnected() and time.time() < deadline:
            time.sleep(0.25)
    return wlan.ifconfig()


def uptime_s():
    if hasattr(time, "ticks_ms"):
        elapsed = time.ticks_ms()
    else:
        elapsed = int(time.time() * 1000)
    return int((elapsed + CLOCK_OFFSET["value"]) / 1000)


def stats():
    gc.collect()
    if hasattr(gc, "mem_free"):
        mem_free = gc.mem_free()
        mem_alloc = gc.mem_alloc()
    else:
        mem_free = None
        mem_alloc = None
    return {
        "uptime_s": uptime_s(),
        "boot_count": _read_boot_count(),
        "mem_free": mem_free,
        "mem_alloc": mem_alloc,
        "sensor": SENSOR_MODE["value"],
        "agent": "espbench-agent/" + FW_VERSION,
        "fw": FW_VERSION,
    }


def _response(status, body, content_type="application/json"):
    if isinstance(body, dict):
        payload = json.dumps(body).encode()
    elif isinstance(body, str):
        payload = body.encode()
    else:
        payload = body
    head = (
        "HTTP/1.1 " + status + "\r\n"
        "Content-Type: " + content_type + "\r\n"
        "Content-Length: " + str(len(payload)) + "\r\n"
        "X-Boot-Count: " + str(_read_boot_count()) + "\r\n"
        "Connection: close\r\n\r\n"
    )
    return head.encode() + payload


def _parse_request(data):
    try:
        head = data.split(b"\r\n\r\n", 1)[0]
        body = data[len(head) + 4:]
    except IndexError:
        return None, {}, b""
    lines = head.split(b"\r\n")
    request_line = lines[0].decode(errors="replace").split()
    if len(request_line) < 2:
        return None, {}, b""
    method, target = request_line[0], request_line[1]
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            key, _, value = line.partition(b":")
            headers[key.strip().lower().decode(errors="replace")] = (
                value.strip().decode(errors="replace"))
    try:
        expected = int(headers.get("content-length", "0") or "0")
    except ValueError:
        return None, {}, b""
    if len(body) < expected:
        return None, {}, b""
    return (method, target, headers), headers, body[:expected]


def _query(target):
    params = {}
    path = target
    if "?" in target:
        path, _, query = target.partition("?")
        for pair in query.split("&"):
            if "=" in pair:
                key, _, value = pair.partition("=")
                params[key] = value.replace("+", " ").replace("%20", " ")
    return params, path


def _content_length(head):
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            try:
                return int(line.split(b":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def handle(client):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = client.recv(1024)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    head_end = data.find(b"\r\n\r\n")
    if head_end >= 0:
        expected = _content_length(data[:head_end])
        while len(data) < head_end + 4 + expected:
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:
                break
    request, _, body = _parse_request(data)
    if request is None:
        client.send(_response("400 Bad Request", {"error": "bad request"}))
        return
    method, target, _headers = request
    params, path = _query(target)

    if method == "GET" and path == "/stats":
        client.send(_response("200 OK", stats()))
    elif method == "GET" and path == "/version":
        client.send(_response("200 OK", {"fw": FW_VERSION}))
    elif method == "GET" and path == "/log":
        client.send(_response("200 OK", {"fw": FW_VERSION, "log": list(LOG)}))
    elif method == "GET" and path == "/ping":
        client.send(_response("200 OK", {"t": uptime_s()}))
    elif method == "POST" and path == "/echo":
        client.send(_response("200 OK", body, "application/octet-stream"))
    elif method == "GET" and path == "/mark":
        label = params.get("label", "op")
        line = "MARK " + str(label) + " " + str(uptime_s())
        print(line)
        _log(line)
        client.send(_response("200 OK", {"marked": label}))
    elif method == "GET" and path == "/sensor":
        mode = params.get("mode", "ok")
        SENSOR_MODE["value"] = mode
        _log("SENSOR " + str(mode))
        client.send(_response("200 OK", {"sensor": mode}))
    elif method == "GET" and path == "/clock":
        try:
            offset = int(params.get("offset", "0"))
        except ValueError:
            client.send(_response("400 Bad Request",
                                  {"error": "offset must be an integer"}))
        else:
            CLOCK_OFFSET["value"] = offset
            client.send(_response("200 OK", {"offset": offset}))
    elif method == "GET" and path == "/restart":
        client.send(_response("200 OK", {"restarting": True}))
        _log("RESTART " + str(uptime_s()))
        time.sleep(0.2)
        import machine

        machine.reset()
    else:
        client.send(_response("404 Not Found", {"error": "not found", "path": path}))


def serve(port=80):
    count = _bump_boot_count()
    _log("BOOT boot_count=" + str(count))
    print("espbench agent up, boot_count=" + str(count))
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("", port))
    server.listen(4)
    while True:
        client, _addr = server.accept()
        try:
            handle(client)
        except Exception as exc:
            _log("ERROR " + str(exc))
            try:
                client.send(_response("500 Internal Server Error", {"error": str(exc)}))
            except Exception:
                pass
        finally:
            try:
                client.close()
            except Exception:
                pass
