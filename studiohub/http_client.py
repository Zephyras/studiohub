"""urllib 기반 최소 HTTP 클라이언트 (JSON·multipart·바이너리 다운로드).

외부 의존성 없이 Immich·AppFlowy REST 를 호출한다. 테스트는 가짜 서버(tests/fake_*.py)에 붙인다.
"""
from __future__ import annotations

import json
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class ApiError(Exception):
    def __init__(self, status: int, url: str, body: bytes | str = b""):
        self.status = status
        self.url = url
        self.body = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        super().__init__(f"HTTP {status} {url}: {self.body[:300]}")


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


def encode_multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str | None]]) -> tuple[bytes, str]:
    """RFC 2388 multipart/form-data 인코딩. files = {field: (filename, data, content_type)}."""
    boundary = "----studiohub" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode()
        out += str(value).encode("utf-8") + b"\r\n"
    for name, (filename, data, ctype) in files.items():
        ctype = ctype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        out += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class Http:
    """base_url 아래로 요청을 보내는 얇은 래퍼. 기본 헤더(API 키·Bearer)는 생성 시 받는다."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout = timeout

    def url(self, path: str, params: dict[str, Any] | None = None) -> str:
        u = path if path.startswith("http") else self.base_url + "/" + path.lstrip("/")
        if params:
            clean = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items() if v is not None}
            if clean:
                u += ("&" if "?" in u else "?") + parse.urlencode(clean)
        return u

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: bytes | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response:
        url = self.url(path, params)
        hdrs = {"Accept": "application/json", **self.headers, **(headers or {})}
        body = data
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif content_type:
            hdrs["Content-Type"] = content_type
        req = request.Request(url, data=body, method=method.upper(), headers=hdrs)
        try:
            with request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
        except error.HTTPError as e:
            raise ApiError(e.code, url, e.read()) from None
        except error.URLError as e:
            raise ApiError(0, url, str(e.reason)) from None

    def get_json(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw).json()

    def post_json(self, path: str, json_body: Any = None, **kw) -> Any:
        return self.request("POST", path, json_body=json_body, **kw).json()

    def put_json(self, path: str, json_body: Any = None, **kw) -> Any:
        return self.request("PUT", path, json_body=json_body, **kw).json()

    def download(self, path: str, dest: Path, *, params: dict[str, Any] | None = None) -> Path:
        resp = self.request("GET", path, params=params, headers={"Accept": "*/*"})
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.body)
        return dest
