import time

import requests


class DeviceError(Exception):
    pass


class Device:
    def __init__(self, host, port=80, timeout=5.0):
        self.host = host
        self.port = port
        self.base = f"http://{host}:{port}"
        self.timeout = timeout
        self.session = requests.Session()

    def get(self, path, params=None):
        try:
            response = self.session.get(self.base + path, params=params,
                                        timeout=self.timeout)
        except requests.RequestException as exc:
            raise DeviceError(f"GET {path}: {exc}") from exc
        return response

    def stats(self):
        response = self.get("/stats")
        if response.status_code != 200:
            raise DeviceError(f"/stats returned {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise DeviceError(f"/stats: invalid JSON body ({exc})") from exc

    def version(self):
        response = self.get("/version")
        if response.status_code != 200:
            raise DeviceError(f"/version returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeviceError(f"/version: invalid JSON body ({exc})") from exc
        fw = payload.get("fw") if isinstance(payload, dict) else None
        if not isinstance(fw, str) or not fw:
            raise DeviceError("/version: missing fw field")
        return fw

    def log(self):
        response = self.get("/log")
        if response.status_code != 200:
            raise DeviceError(f"/log returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeviceError(f"/log: invalid JSON body ({exc})") from exc
        if not isinstance(payload, dict):
            raise DeviceError("/log: expected a JSON object")
        return payload

    def ping(self):
        start = time.perf_counter()
        response = self.get("/ping")
        if response.status_code != 200:
            raise DeviceError(f"/ping returned {response.status_code}")
        return (time.perf_counter() - start) * 1000.0

    def echo(self, payload):
        data = payload.encode() if isinstance(payload, str) else payload
        try:
            response = self.session.post(self.base + "/echo", data=data,
                                         timeout=self.timeout)
        except requests.RequestException as exc:
            raise DeviceError(f"/echo: {exc}") from exc
        return response.status_code

    def mark(self, label):
        response = self.get("/mark", params={"label": label})
        return response.status_code == 200

    def set_sensor(self, mode):
        response = self.get("/sensor", params={"mode": mode})
        return response.status_code == 200

    def restart(self):
        try:
            response = self.get("/restart")
        except DeviceError:
            return True
        return response.status_code == 200
