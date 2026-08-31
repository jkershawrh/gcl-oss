from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gcl_oss.adapters.oci import (
    OCIArtifactVerificationError,
    OCIRegistryVerifier,
    parse_oci_reference,
)
from gcl_oss.ports import ArtifactVerificationRequest


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass
class RegistryFixture:
    config: bytes = b"{}"
    layer: bytes = b'{"evaluation":"verified"}'
    content_type: str = "application/vnd.oci.image.manifest.v1+json"
    manifest_override: bytes | None = None
    layer_override: bytes | None = None
    require_auth: bool = False
    expected_basic: str | None = None
    token_realm: str | None = None

    def manifest(self) -> bytes:
        if self.manifest_override is not None:
            return self.manifest_override
        return json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": self.content_type,
                "config": {
                    "mediaType": "application/vnd.oci.empty.v1+json",
                    "digest": digest(self.config),
                    "size": len(self.config),
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.eval-hub.evaluation-card.v1+json",
                        "digest": digest(self.layer),
                        "size": len(self.layer),
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()


@contextmanager
def registry_server(fixture: RegistryFixture):
    requests: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            authorization = self.headers.get("Authorization")
            requests.append((self.path, authorization))
            if self.path.startswith("/token"):
                if fixture.expected_basic and authorization != fixture.expected_basic:
                    self.send_error(401)
                    return
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if query.get("scope") != ["repository:org/results:pull"]:
                    self.send_error(400)
                    return
                self._reply(
                    json.dumps({"token": "registry-token"}).encode(),
                    "application/json",
                )
                return
            if fixture.require_auth and authorization != "Bearer registry-token":
                realm = fixture.token_realm or (
                    f"http://127.0.0.1:{self.server.server_port}/token"
                )
                self.send_response(401)
                self.send_header(
                    "WWW-Authenticate",
                    f'Bearer realm="{realm}",service="test-registry"',
                )
                self.end_headers()
                return

            manifest = fixture.manifest()
            manifest_digest = digest(manifest)
            if self.path.startswith("/v2/org/results/manifests/"):
                self._reply(
                    manifest,
                    fixture.content_type,
                    content_digest=manifest_digest,
                )
                return
            if self.path == f"/v2/org/results/blobs/{digest(fixture.config)}":
                self._reply(
                    fixture.config,
                    "application/octet-stream",
                    content_digest=digest(fixture.config),
                )
                return
            if self.path == f"/v2/org/results/blobs/{digest(fixture.layer)}":
                payload = fixture.layer_override or fixture.layer
                self._reply(
                    payload,
                    "application/octet-stream",
                    content_digest=digest(fixture.layer),
                )
                return
            self.send_error(404)

        def _reply(
            self,
            payload: bytes,
            content_type: str,
            *,
            content_digest: str | None = None,
        ) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            if content_digest:
                self.send_header("Docker-Content-Digest", content_digest)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def verifier(port: int, **kwargs) -> OCIRegistryVerifier:
    return OCIRegistryVerifier(
        allowed_registries=[f"127.0.0.1:{port}"],
        allow_insecure_http=True,
        clock=lambda: datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        **kwargs,
    )


def request(port: int, fixture: RegistryFixture) -> ArtifactVerificationRequest:
    manifest_digest = digest(fixture.manifest())
    return ArtifactVerificationRequest(
        artifact_uri=(
            f"http://127.0.0.1:{port}/org/results@{manifest_digest}"
        ),
        expected_digest=manifest_digest,
    )


def test_parse_requires_explicit_digest_pinned_registry_reference() -> None:
    value = parse_oci_reference(
        "oci://quay.io/example/results@sha256:" + "a" * 64
    )
    assert value.scheme == "https"
    assert value.registry == "quay.io"
    assert value.repository == "example/results"

    for invalid in (
        "example/results:latest",
        "https://user:secret@quay.io/example/results@sha256:" + "a" * 64,
        "ftp://quay.io/example/results@sha256:" + "a" * 64,
        "quay.io/Example/results@sha256:" + "a" * 64,
        "quay.io/example/../results@sha256:" + "a" * 64,
    ):
        with pytest.raises(OCIArtifactVerificationError):
            parse_oci_reference(invalid)


def test_verifies_manifest_and_every_descriptor_payload() -> None:
    fixture = RegistryFixture()
    with registry_server(fixture) as (port, requests):
        receipt = asyncio.run(verifier(port).verify(request(port, fixture)))

    assert receipt.verified is True
    assert receipt.artifact_digest == digest(fixture.manifest())
    assert receipt.registry_digest == receipt.artifact_digest
    assert [content.role for content in receipt.content] == ["config", "layer"]
    assert [content.digest for content in receipt.content] == [
        digest(fixture.config),
        digest(fixture.layer),
    ]
    assert all(content.registry_digest == content.digest for content in receipt.content)
    assert len(requests) == 3


def test_rejects_manifest_bytes_that_do_not_match_the_requested_digest() -> None:
    fixture = RegistryFixture()
    original = fixture.manifest()
    with registry_server(fixture) as (port, _):
        verification_request = ArtifactVerificationRequest(
            artifact_uri=(
                f"http://127.0.0.1:{port}/org/results@{digest(original)}"
            ),
            expected_digest=digest(original),
        )
        fixture.manifest_override = original + b" "
        with pytest.raises(OCIArtifactVerificationError, match="manifest bytes"):
            asyncio.run(verifier(port).verify(verification_request))


def test_rejects_descriptor_bytes_that_do_not_match_the_manifest() -> None:
    fixture = RegistryFixture(layer_override=b'{"evaluation":"tampered"}')
    with registry_server(fixture) as (port, _):
        with pytest.raises(OCIArtifactVerificationError, match="digest header"):
            asyncio.run(verifier(port).verify(request(port, fixture)))


def test_rejects_unapproved_registry_before_network_access() -> None:
    fixture = RegistryFixture()
    with registry_server(fixture) as (port, requests):
        other = OCIRegistryVerifier(
            allowed_registries=["registry.example"],
            allow_insecure_http=True,
        )
        with pytest.raises(OCIArtifactVerificationError, match="not explicitly allowed"):
            asyncio.run(other.verify(request(port, fixture)))
    assert requests == []


def test_rejects_unsupported_manifest_media_type() -> None:
    fixture = RegistryFixture(content_type="application/vnd.oci.image.index.v1+json")
    with registry_server(fixture) as (port, _):
        with pytest.raises(OCIArtifactVerificationError, match="media type"):
            asyncio.run(verifier(port).verify(request(port, fixture)))


def test_enforces_descriptor_and_total_size_limits() -> None:
    fixture = RegistryFixture()
    with registry_server(fixture) as (port, _):
        with pytest.raises(OCIArtifactVerificationError, match="blob size limit"):
            asyncio.run(
                verifier(port, max_blob_bytes=len(fixture.layer) - 1).verify(
                    request(port, fixture)
                )
            )


def test_bearer_challenge_uses_file_credentials_and_pull_only_scope(
    tmp_path: Path,
) -> None:
    username = "serviceaccount"
    password = "projected-token"
    expected_basic = "Basic " + base64.b64encode(
        f"{username}:{password}".encode()
    ).decode()
    fixture = RegistryFixture(require_auth=True, expected_basic=expected_basic)
    auth_file = tmp_path / "config.json"

    with registry_server(fixture) as (port, requests):
        auth_file.write_text(
            json.dumps(
                {
                    "auths": {
                        f"127.0.0.1:{port}": {
                            "auth": base64.b64encode(
                                f"{username}:{password}".encode()
                            ).decode()
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        receipt = asyncio.run(
            verifier(port, auth_file=auth_file).verify(request(port, fixture))
        )

    assert receipt.verified is True
    assert any(path.startswith("/token?") and auth == expected_basic for path, auth in requests)
    assert any(auth == "Bearer registry-token" for _, auth in requests)


def test_rejects_token_realm_host_that_is_not_explicitly_allowed() -> None:
    fixture = RegistryFixture(
        require_auth=True,
        token_realm="https://auth.attacker.invalid/token",
    )
    with registry_server(fixture) as (port, requests):
        with pytest.raises(OCIArtifactVerificationError, match="realm host"):
            asyncio.run(verifier(port).verify(request(port, fixture)))

    assert len(requests) == 1
