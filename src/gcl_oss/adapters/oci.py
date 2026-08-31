"""Digest-verifying client for leaf manifests in an OCI Distribution registry."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gcl_oss.ports import (
    ArtifactVerificationReceipt,
    ArtifactVerificationRequest,
    VerifiedArtifactContent,
)

OCI_DISTRIBUTION_VERIFIER = (
    "https://jkershawrh.github.io/gcl-oss/verifiers/oci-distribution/v1"
)
OCI_IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_IMAGE_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
OCI_EVAL_CARD_LAYER = "application/vnd.eval-hub.evaluation-card.v1+json"

_ALLOWED_MANIFEST_MEDIA_TYPES = frozenset(
    {OCI_IMAGE_MANIFEST, DOCKER_IMAGE_MANIFEST}
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_SEGMENT_RE = re.compile(
    r"^[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*$"
)


class OCIArtifactVerificationError(ValueError):
    """An OCI artifact cannot be cryptographically or structurally verified."""


class OCITransportError(OCIArtifactVerificationError):
    """An OCI registry or token-service request failed."""


@dataclass(frozen=True)
class OCIReference:
    original: str
    scheme: str
    registry: str
    repository: str
    digest: str


@dataclass(frozen=True)
class _RegistryCredentials:
    username: str | None = None
    password: str | None = None
    identity_token: str | None = None


def _registry_authority(parsed: urllib.parse.SplitResult) -> str:
    hostname = parsed.hostname
    if not hostname:
        raise OCIArtifactVerificationError("OCI reference requires an explicit registry")
    hostname = hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError as exc:
        raise OCIArtifactVerificationError("OCI registry port is invalid") from exc
    return f"{hostname}:{port}" if port is not None else hostname


def parse_oci_reference(reference: str) -> OCIReference:
    """Parse a digest-pinned reference without applying Docker Hub defaults."""

    if not reference or len(reference) > 2048 or any(
        character.isspace() for character in reference
    ):
        raise OCIArtifactVerificationError(
            "OCI reference is empty, too long, or contains whitespace"
        )

    candidate = reference if "://" in reference else "oci://" + reference
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme not in {"oci", "https", "http"}:
        raise OCIArtifactVerificationError("OCI reference has an unsupported scheme")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OCIArtifactVerificationError(
            "OCI reference cannot contain credentials, query, or fragment"
        )
    if "%" in parsed.netloc or "%" in parsed.path:
        raise OCIArtifactVerificationError("OCI reference cannot contain percent encoding")

    registry = _registry_authority(parsed)
    path = parsed.path.lstrip("/")
    if path.count("@") != 1:
        raise OCIArtifactVerificationError("OCI reference must contain exactly one digest")
    repository, digest = path.rsplit("@", 1)
    segments = repository.split("/")
    if not repository or any(
        not segment or not _REPOSITORY_SEGMENT_RE.fullmatch(segment)
        for segment in segments
    ):
        raise OCIArtifactVerificationError("OCI repository name is invalid")
    if not _DIGEST_RE.fullmatch(digest):
        raise OCIArtifactVerificationError("OCI reference requires a lowercase sha256 digest")

    scheme = "https" if parsed.scheme == "oci" else parsed.scheme
    return OCIReference(
        original=reference,
        scheme=scheme,
        registry=registry,
        repository=repository,
        digest=digest,
    )


def _normalize_allowed_registry(value: str) -> str:
    if not value or "://" in value or any(character.isspace() for character in value):
        raise ValueError("allowed registry must be an exact host[:port]")
    parsed = urllib.parse.urlsplit("//" + value)
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("allowed registry must not contain a path or credentials")
    return _registry_authority(parsed)


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(req.full_url) != _origin(newurl):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "OCI registry redirect changed origin",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _secret_file(path: Path, *, maximum: int, label: str) -> str:
    try:
        if path.stat().st_size > maximum:
            raise OCIArtifactVerificationError(f"{label} exceeds {maximum} bytes")
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OCIArtifactVerificationError(f"cannot read {label}") from exc
    if not value:
        raise OCIArtifactVerificationError(f"{label} is empty")
    if any(character in value for character in "\r\n\x00"):
        raise OCIArtifactVerificationError(f"{label} contains invalid characters")
    return value


def _docker_config_credentials(path: Path, registry: str) -> _RegistryCredentials:
    try:
        if path.stat().st_size > 1024 * 1024:
            raise OCIArtifactVerificationError("registry auth file exceeds 1 MiB")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OCIArtifactVerificationError("registry auth file is not valid JSON") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("auths"), Mapping):
        raise OCIArtifactVerificationError("registry auth file requires an auths object")

    auths = payload["auths"]
    entry: Any = None
    for candidate in (registry, f"https://{registry}", f"http://{registry}"):
        if candidate in auths:
            entry = auths[candidate]
            break
    if not isinstance(entry, Mapping):
        return _RegistryCredentials()

    identity_token = entry.get("identitytoken") or entry.get("identityToken")
    if identity_token is not None:
        if not isinstance(identity_token, str) or any(
            character in identity_token for character in "\r\n\x00"
        ):
            raise OCIArtifactVerificationError("registry identity token is invalid")
        return _RegistryCredentials(identity_token=identity_token)

    username = entry.get("username")
    password = entry.get("password")
    encoded_auth = entry.get("auth")
    if encoded_auth is not None:
        if not isinstance(encoded_auth, str):
            raise OCIArtifactVerificationError("registry auth entry is invalid")
        try:
            decoded = base64.b64decode(encoded_auth, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeError) as exc:
            raise OCIArtifactVerificationError("registry auth entry is not valid base64") from exc
        username, separator, password = decoded.partition(":")
        if not separator:
            raise OCIArtifactVerificationError("registry auth entry has no password separator")
    if username is None and password is None:
        return _RegistryCredentials()
    if not isinstance(username, str) or not isinstance(password, str):
        raise OCIArtifactVerificationError("registry username and password must be strings")
    if any(character in username + password for character in "\r\n\x00"):
        raise OCIArtifactVerificationError("registry credentials contain invalid characters")
    return _RegistryCredentials(username=username, password=password)


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _header_digest(headers: Mapping[str, str], expected: str, actual: str) -> str | None:
    value = headers.get("Docker-Content-Digest")
    if value is None:
        return None
    value = value.strip()
    if not _DIGEST_RE.fullmatch(value):
        raise OCIArtifactVerificationError("registry returned an invalid content digest header")
    if value != expected or value != actual:
        raise OCIArtifactVerificationError("registry content digest header does not match bytes")
    return value


def _content_type(headers: Mapping[str, str]) -> str:
    return headers.get("Content-Type", "").partition(";")[0].strip().lower()


class OCIRegistryVerifier:
    """Verify a digest-pinned leaf manifest and every config/layer descriptor.

    Registry hosts must be explicitly allowed. The client performs the OCI Distribution
    Bearer challenge flow and reads optional credentials from a Docker config file or a
    username plus password file. Cross-origin redirects are rejected so credentials are
    never forwarded to a registry-selected host.
    """

    def __init__(
        self,
        *,
        allowed_registries: Sequence[str],
        allowed_auth_hosts: Sequence[str] = (),
        auth_file: Path | str | None = None,
        username: str | None = None,
        password_file: Path | str | None = None,
        ca_file: Path | str | None = None,
        timeout: float = 10.0,
        max_manifest_bytes: int = 4 * 1024 * 1024,
        max_blob_bytes: int = 32 * 1024 * 1024,
        max_total_bytes: int = 64 * 1024 * 1024,
        allow_insecure_http: bool = False,
        clock=None,
    ) -> None:
        normalized_registries = {
            _normalize_allowed_registry(value) for value in allowed_registries
        }
        if not normalized_registries:
            raise ValueError("at least one allowed registry is required")
        normalized_auth_hosts = {
            _normalize_allowed_registry(value) for value in allowed_auth_hosts
        }
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if min(max_manifest_bytes, max_blob_bytes, max_total_bytes) <= 0:
            raise ValueError("OCI response limits must be positive")
        if max_total_bytes < max_manifest_bytes:
            raise ValueError("max_total_bytes cannot be smaller than max_manifest_bytes")
        if auth_file is not None and (username is not None or password_file is not None):
            raise ValueError("registry auth file cannot be combined with direct credentials")
        if (username is None) != (password_file is None):
            raise ValueError("registry username and password file must be provided together")
        if username is not None and any(character in username for character in "\r\n\x00"):
            raise ValueError("registry username contains invalid characters")

        context = ssl.create_default_context()
        if ca_file is not None:
            context.load_verify_locations(cafile=str(ca_file))
        self._opener = urllib.request.build_opener(
            _SameOriginRedirectHandler(),
            urllib.request.HTTPSHandler(context=context),
        )
        self._allowed_registries = frozenset(normalized_registries)
        self._allowed_auth_hosts = frozenset(
            normalized_registries | normalized_auth_hosts
        )
        self._auth_file = Path(auth_file) if auth_file is not None else None
        self._username = username
        self._password_file = (
            Path(password_file) if password_file is not None else None
        )
        self._timeout = timeout
        self._max_manifest_bytes = max_manifest_bytes
        self._max_blob_bytes = max_blob_bytes
        self._max_total_bytes = max_total_bytes
        self._allow_insecure_http = allow_insecure_http
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tokens: dict[tuple[str, str], str] = {}

    async def verify(
        self,
        request: ArtifactVerificationRequest,
    ) -> ArtifactVerificationReceipt:
        return await asyncio.to_thread(self._verify_sync, request)

    def _credentials(self, registry: str) -> _RegistryCredentials:
        if self._auth_file is not None:
            return _docker_config_credentials(self._auth_file, registry)
        if self._username is not None and self._password_file is not None:
            return _RegistryCredentials(
                username=self._username,
                password=_secret_file(
                    self._password_file,
                    maximum=64 * 1024,
                    label="registry password file",
                ),
            )
        return _RegistryCredentials()

    def _verify_sync(
        self,
        request: ArtifactVerificationRequest,
    ) -> ArtifactVerificationReceipt:
        reference = parse_oci_reference(request.artifact_uri)
        if reference.digest != request.expected_digest:
            raise OCIArtifactVerificationError(
                "OCI reference digest does not match the verification request"
            )
        if reference.registry not in self._allowed_registries:
            raise OCIArtifactVerificationError("OCI registry is not explicitly allowed")
        if reference.scheme == "http" and not self._allow_insecure_http:
            raise OCIArtifactVerificationError(
                "OCI registry requires HTTPS unless explicitly overridden"
            )

        credentials = self._credentials(reference.registry)
        manifest_url = (
            f"{reference.scheme}://{reference.registry}/v2/{reference.repository}"
            f"/manifests/{reference.digest}"
        )
        manifest_body, manifest_headers = self._get(
            manifest_url,
            registry=reference.registry,
            repository=reference.repository,
            credentials=credentials,
            accept=", ".join(sorted(_ALLOWED_MANIFEST_MEDIA_TYPES)),
            maximum=self._max_manifest_bytes,
        )
        if not manifest_body:
            raise OCIArtifactVerificationError("OCI manifest is empty")
        actual_manifest_digest = _sha256(manifest_body)
        if actual_manifest_digest != reference.digest:
            raise OCIArtifactVerificationError("OCI manifest bytes do not match its digest")
        registry_digest = _header_digest(
            manifest_headers,
            reference.digest,
            actual_manifest_digest,
        )
        manifest_media_type = _content_type(manifest_headers)
        if manifest_media_type not in _ALLOWED_MANIFEST_MEDIA_TYPES:
            raise OCIArtifactVerificationError("OCI manifest media type is unsupported")

        try:
            manifest = json.loads(manifest_body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OCIArtifactVerificationError("OCI manifest is not valid JSON") from exc
        if not isinstance(manifest, Mapping) or manifest.get("schemaVersion") != 2:
            raise OCIArtifactVerificationError("OCI manifest requires schemaVersion 2")
        if manifest.get("mediaType") != manifest_media_type:
            raise OCIArtifactVerificationError(
                "OCI manifest body and response media types do not match"
            )

        descriptors: list[tuple[str, Mapping[str, Any]]] = []
        config = manifest.get("config")
        layers = manifest.get("layers")
        if not isinstance(config, Mapping) or not isinstance(layers, list):
            raise OCIArtifactVerificationError(
                "OCI leaf manifest requires config and layers descriptors"
            )
        descriptors.append(("config", config))
        for layer in layers:
            if not isinstance(layer, Mapping):
                raise OCIArtifactVerificationError("OCI layer descriptor is not an object")
            descriptors.append(("layer", layer))

        total = len(manifest_body)
        verified_content: list[VerifiedArtifactContent] = []
        for role, descriptor in descriptors:
            content, declared_digest, declared_size, media_type, content_header_digest = (
                self._verify_descriptor(reference, credentials, descriptor)
            )
            total += len(content)
            if total > self._max_total_bytes:
                raise OCIArtifactVerificationError(
                    "OCI artifact exceeds the configured total size limit"
                )
            verified_content.append(
                VerifiedArtifactContent(
                    role=role,
                    digest=declared_digest,
                    media_type=media_type,
                    size_bytes=declared_size,
                    registry_digest=content_header_digest,
                )
            )

        verified_at = self._clock()
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise OCIArtifactVerificationError("OCI verifier clock must be timezone-aware")
        return ArtifactVerificationReceipt(
            verifier=OCI_DISTRIBUTION_VERIFIER,
            artifact_uri=request.artifact_uri,
            artifact_digest=reference.digest,
            manifest_media_type=manifest_media_type,
            manifest_size_bytes=len(manifest_body),
            registry_digest=registry_digest,
            verified_at=verified_at.astimezone(timezone.utc),
            content=verified_content,
        )

    def _verify_descriptor(
        self,
        reference: OCIReference,
        credentials: _RegistryCredentials,
        descriptor: Mapping[str, Any],
    ) -> tuple[bytes, str, int, str, str | None]:
        digest = descriptor.get("digest")
        size = descriptor.get("size")
        media_type = descriptor.get("mediaType")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise OCIArtifactVerificationError("OCI descriptor has an invalid digest")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise OCIArtifactVerificationError("OCI descriptor has an invalid size")
        if (
            not isinstance(media_type, str)
            or not media_type
            or len(media_type) > 255
            or any(character.isspace() for character in media_type)
        ):
            raise OCIArtifactVerificationError("OCI descriptor has an invalid media type")
        if size > self._max_blob_bytes:
            raise OCIArtifactVerificationError(
                "OCI descriptor exceeds the configured blob size limit"
            )

        encoded_data = descriptor.get("data")
        if encoded_data is not None:
            if not isinstance(encoded_data, str):
                raise OCIArtifactVerificationError("OCI descriptor data is not base64 text")
            try:
                content = base64.b64decode(encoded_data, validate=True)
            except binascii.Error as exc:
                raise OCIArtifactVerificationError(
                    "OCI descriptor data is not valid base64"
                ) from exc
            header_digest = None
        else:
            blob_url = (
                f"{reference.scheme}://{reference.registry}/v2/{reference.repository}"
                f"/blobs/{digest}"
            )
            content, headers = self._get(
                blob_url,
                registry=reference.registry,
                repository=reference.repository,
                credentials=credentials,
                accept="application/octet-stream",
                maximum=size,
            )
            header_digest = _header_digest(headers, digest, _sha256(content))

        if len(content) != size:
            raise OCIArtifactVerificationError("OCI descriptor size does not match bytes")
        if _sha256(content) != digest:
            raise OCIArtifactVerificationError("OCI descriptor bytes do not match its digest")
        return content, digest, size, media_type, header_digest

    def _get(
        self,
        url: str,
        *,
        registry: str,
        repository: str,
        credentials: _RegistryCredentials,
        accept: str,
        maximum: int,
    ) -> tuple[bytes, Mapping[str, str]]:
        key = (registry, repository)
        token = self._tokens.get(key) or credentials.identity_token
        try:
            return self._send(url, accept=accept, maximum=maximum, bearer_token=token)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                exc.close()
                raise OCITransportError(
                    f"OCI registry request failed with HTTP {exc.code}"
                ) from exc
            challenge = exc.headers.get("WWW-Authenticate", "")
            exc.close()

        token = self._exchange_token(
            challenge,
            registry=registry,
            repository=repository,
            credentials=credentials,
        )
        self._tokens[key] = token
        try:
            return self._send(url, accept=accept, maximum=maximum, bearer_token=token)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise OCITransportError(
                f"OCI registry request failed with HTTP {code} after authentication"
            ) from exc

    def _send(
        self,
        url: str,
        *,
        accept: str,
        maximum: int,
        bearer_token: str | None,
    ) -> tuple[bytes, Mapping[str, str]]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "Accept-Encoding": "identity",
                "User-Agent": "gcl-oss-oci-verifier/1",
            },
            method="GET",
        )
        if bearer_token:
            request.add_unredirected_header("Authorization", "Bearer " + bearer_token)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                headers = response.headers
                if headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise OCIArtifactVerificationError(
                        "compressed registry responses are not accepted"
                    )
                declared_length = headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        length = int(declared_length)
                    except ValueError as exc:
                        raise OCIArtifactVerificationError(
                            "registry returned an invalid content length"
                        ) from exc
                    if length < 0 or length > maximum:
                        raise OCIArtifactVerificationError(
                            "registry response exceeds the configured size limit"
                        )
                body = response.read(maximum + 1)
                if len(body) > maximum:
                    raise OCIArtifactVerificationError(
                        "registry response exceeds the configured size limit"
                    )
                return body, headers
        except urllib.error.HTTPError:
            raise
        except OCIArtifactVerificationError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            raise OCITransportError("OCI registry request failed") from exc

    def _exchange_token(
        self,
        challenge: str,
        *,
        registry: str,
        repository: str,
        credentials: _RegistryCredentials,
    ) -> str:
        scheme, separator, raw_parameters = challenge.partition(" ")
        if not separator or scheme.lower() != "bearer":
            raise OCITransportError("OCI registry did not return a Bearer challenge")
        try:
            parameters = urllib.request.parse_keqv_list(
                urllib.request.parse_http_list(raw_parameters)
            )
        except ValueError as exc:
            raise OCITransportError("OCI registry Bearer challenge is invalid") from exc
        realm = parameters.get("realm")
        if not realm:
            raise OCITransportError("OCI registry Bearer challenge has no realm")
        parsed = urllib.parse.urlsplit(realm)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OCITransportError("OCI token realm is not an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise OCITransportError("OCI token realm contains credentials or a fragment")
        if parsed.scheme == "http" and not self._allow_insecure_http:
            raise OCITransportError("OCI token realm requires HTTPS")
        if _registry_authority(parsed) not in self._allowed_auth_hosts:
            raise OCITransportError("OCI token realm host is not explicitly allowed")

        query = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
            if key not in {"service", "scope"}
        ]
        service = parameters.get("service")
        if service:
            query.append(("service", service))
        query.append(("scope", f"repository:{repository}:pull"))
        token_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), "")
        )
        request = urllib.request.Request(
            token_url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "gcl-oss-oci-verifier/1",
            },
            method="GET",
        )
        if credentials.username is not None and credentials.password is not None:
            encoded = base64.b64encode(
                f"{credentials.username}:{credentials.password}".encode()
            ).decode("ascii")
            request.add_unredirected_header("Authorization", "Basic " + encoded)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise OCITransportError("OCI token response exceeds 1 MiB")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise OCITransportError(
                f"OCI token request failed with HTTP {code}"
            ) from exc
        except OCITransportError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            raise OCITransportError("OCI token request failed") from exc
        try:
            token_payload = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OCITransportError("OCI token response is not valid JSON") from exc
        if not isinstance(token_payload, Mapping):
            raise OCITransportError("OCI token response is not an object")
        token = token_payload.get("token") or token_payload.get("access_token")
        if (
            not isinstance(token, str)
            or not token
            or any(character in token for character in "\r\n\x00")
        ):
            raise OCITransportError("OCI token response has no valid token")
        return token
