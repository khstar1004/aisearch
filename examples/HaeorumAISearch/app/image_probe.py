from __future__ import annotations

import urllib.error
import urllib.request
import socket
from dataclasses import dataclass
from http.client import HTTPMessage
import time
from urllib.parse import urlparse

from .image_validation import validate_image_bytes
from .models import ProductDocument
from .url_safety import is_non_public_ip_address, safe_absolute_http_url


@dataclass(frozen=True)
class ImageProbeResult:
    ok: bool
    product_id: str
    image_url: str | None
    message: str | None = None
    attempts: int = 1
    warnings: tuple[str, ...] = ()


class UnsafeImageRedirectError(ValueError):
    pass


class UnsafeImageHostError(ValueError):
    pass


class SafeImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp,  # type: ignore[no-untyped-def]
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirect = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirect is None:
            return None
        if safe_absolute_http_url(redirect.full_url) is None:
            raise UnsafeImageRedirectError("image URL redirected to a target that is not an absolute safe http(s) URL")
        validate_image_url_resolves_to_public_network(redirect.full_url)
        return redirect


IMAGE_OPENER = urllib.request.build_opener(SafeImageRedirectHandler)


def open_image_request(request: urllib.request.Request, timeout_seconds: int):  # type: ignore[no-untyped-def]
    validate_image_url_resolves_to_public_network(request.full_url)
    return IMAGE_OPENER.open(request, timeout=timeout_seconds)


def validate_image_url_resolves_to_public_network(
    image_url: str,
    resolver=None,  # type: ignore[no-untyped-def]
) -> None:
    if resolver is None:
        resolver = socket.getaddrinfo
    parsed = urlparse(image_url)
    host = parsed.hostname
    if not host:
        raise UnsafeImageHostError("image URL host is required")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise urllib.error.URLError(f"DNS resolution failed for image URL host: {exc}") from exc
    resolved_ips = sorted(
        {
            str(sockaddr[0])
            for *_, sockaddr in addresses
            if isinstance(sockaddr, tuple) and sockaddr
        }
    )
    if not resolved_ips:
        raise urllib.error.URLError("DNS resolution returned no addresses for image URL host")
    non_public_ips = [address for address in resolved_ips if is_non_public_ip_address(address)]
    if non_public_ips:
        raise UnsafeImageHostError(
            "image URL host resolves to a non-public address: " + ", ".join(non_public_ips[:5])
        )


class ProductImageProbe:
    def __init__(
        self,
        max_bytes: int,
        timeout_seconds: int = 10,
        retry_count: int = 1,
        retry_delay_seconds: float = 0.25,
        min_dimension: int = 16,
    ):
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.retry_count = max(0, retry_count)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.min_dimension = max(1, min_dimension)

    def validate(self, product: ProductDocument) -> ImageProbeResult:
        if not product.image_url:
            return ImageProbeResult(False, product.product_id, product.image_url, "missing image URL")
        image_url = safe_absolute_http_url(product.image_url)
        if not image_url:
            return ImageProbeResult(False, product.product_id, product.image_url, "image URL must be an absolute safe http(s) URL")
        last_failure = None
        max_attempts = self.retry_count + 1
        for attempt in range(1, max_attempts + 1):
            result = self._download_and_validate(product, image_url, attempt)
            if result.ok:
                return result
            last_failure = result
            if attempt < max_attempts and is_retryable_message(result.message):
                time.sleep(self.retry_delay_seconds)
                continue
            return result
        assert last_failure is not None
        return last_failure

    def _download_and_validate(self, product: ProductDocument, image_url: str, attempt: int) -> ImageProbeResult:
        request = urllib.request.Request(
            image_url,
            headers={
                "User-Agent": "HaeorumAISearchImageProbe/1.0",
                "Accept": "image/webp,image/png,image/jpeg,*/*;q=0.1",
            },
        )
        try:
            with open_image_request(request, self.timeout_seconds) as response:
                final_url = safe_absolute_http_url(response.geturl() if hasattr(response, "geturl") else image_url)
                if final_url is None:
                    return ImageProbeResult(
                        False,
                        product.product_id,
                        image_url,
                        "image URL redirected to a target that is not an absolute safe http(s) URL",
                        attempts=attempt,
                    )
                raw = response.read(self.max_bytes + 1)
                declared_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower() or None
        except urllib.error.HTTPError as exc:
            return ImageProbeResult(
                False,
                product.product_id,
                image_url,
                f"image download failed: HTTP {exc.code}",
                attempts=attempt,
            )
        except UnsafeImageRedirectError as exc:
            return ImageProbeResult(False, product.product_id, image_url, str(exc), attempts=attempt)
        except UnsafeImageHostError as exc:
            return ImageProbeResult(False, product.product_id, image_url, str(exc), attempts=attempt)
        except Exception as exc:
            return ImageProbeResult(
                False,
                product.product_id,
                image_url,
                f"image download failed: {exc}",
                attempts=attempt,
            )
        try:
            image = validate_image_bytes(
                raw,
                max_bytes=self.max_bytes,
                declared_mime_type=declared_type,
                min_dimension=self.min_dimension,
            )
        except Exception as exc:
            return ImageProbeResult(False, product.product_id, image_url, str(exc), attempts=attempt)
        warnings = (*image.quality_warnings, *image_url_quality_warnings(image_url))
        return ImageProbeResult(True, product.product_id, image_url, attempts=attempt, warnings=warnings)


def is_retryable_message(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    if "http 4" in lowered:
        return False
    return "image download failed" in lowered


def image_url_quality_warnings(image_url: str | None) -> tuple[str, ...]:
    if not image_url:
        return ()
    lowered = image_url.lower()
    warnings = []
    if any(marker in lowered for marker in ["watermark", "watermarked", "_wm.", "-wm.", "/wm/", "워터마크"]):
        warnings.append("possible_watermark")
    if any(marker in lowered for marker in ["sample", "placeholder", "noimage", "no_image"]):
        warnings.append("placeholder_or_sample_image")
    return tuple(warnings)
