"""Adobe installer catalog and payload download helpers used by Shotwright."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.client
import inspect
import json
import platform as py_platform
import re
import secrets
import shutil
import string
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.dom.minidom
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Callable, Mapping


Callback = Callable[..., Any]


async def _invoke_callback(callback: Callback | None, *args: Any) -> Any:
    if callback is None:
        return None
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _utcnow() -> datetime:
    return datetime.utcnow()


def compare_version_strings(version1: str, version2: str) -> int:
    components1 = [int(component) if component.isdigit() else 0 for component in version1.split(".")]
    components2 = [int(component) if component.isdigit() else 0 for component in version2.split(".")]

    max_length = max(len(components1), len(components2))
    padded1 = components1 + [0] * (max_length - len(components1))
    padded2 = components2 + [0] * (max_length - len(components2))

    for index in range(max_length):
        if padded1[index] != padded2[index]:
            return padded1[index] - padded2[index]
    return 0


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


class PackageStatus(str, Enum):
    WAITING = "waiting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class PrepareStage(str, Enum):
    INITIALIZING = "initializing"
    CREATING_INSTALLER = "creating_installer"
    SIGNING_APP = "signing_app"
    FETCHING_INFO = "fetching_info"
    VALIDATING_SETUP = "validating_setup"


class PauseReason(str, Enum):
    USER_REQUESTED = "user_requested"
    NETWORK_ISSUE = "network_issue"
    SYSTEM_SLEEP = "system_sleep"
    OTHER = "other"


@dataclass(slots=True)
class PrepareInfo:
    message: str
    timestamp: datetime
    stage: PrepareStage


@dataclass(slots=True)
class DownloadInfo:
    file_name: str
    current_package_index: int
    total_packages: int
    start_time: datetime
    estimated_time_remaining: float | None = None


@dataclass(slots=True)
class PauseInfo:
    reason: str | PauseReason
    timestamp: datetime
    resumable: bool


@dataclass(slots=True)
class CompletionInfo:
    timestamp: datetime
    total_time: float
    total_size: int


@dataclass(slots=True)
class FailureInfo:
    message: str
    error: BaseException | None
    timestamp: datetime
    recoverable: bool


@dataclass(slots=True)
class RetryInfo:
    attempt: int
    max_attempts: int
    reason: str
    next_retry_date: datetime


@dataclass(slots=True)
class TaskStatus:
    phase: str
    info: Any = None

    @classmethod
    def waiting(cls) -> "TaskStatus":
        return cls("waiting")


@dataclass(slots=True)
class DependencyInfo:
    sap_code: str
    base_version: str
    product_version: str
    build_guid: str
    selected_platform: str = ""


@dataclass(slots=True)
class LanguageSet:
    manifest_url: str
    dependencies: list[DependencyInfo]
    build_guid: str
    base_version: str
    product_version: str
    product_code: str = ""
    name: str = ""
    install_size: int = 0


@dataclass(slots=True)
class Platform:
    id: str
    language_sets: list[LanguageSet]


@dataclass(slots=True)
class Product:
    id: str
    version: str
    display_name: str
    platforms: list[Platform]


@dataclass(slots=True)
class ValidationSegment:
    segment_number: int
    hash: str


@dataclass(slots=True)
class ValidationInfo:
    segment_size: int
    version: str
    algorithm: str
    segment_count: int
    last_segment_size: int
    package_hash_key: str
    segments: list[ValidationSegment]

    @classmethod
    def parse(cls, xml_text: str) -> "ValidationInfo | None":
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        def text(path: str, default: str = "") -> str:
            value = root.findtext(path, default=default)
            return (value or default).strip()

        segments: list[ValidationSegment] = []
        for segment in root.findall(".//segment"):
            number = int(segment.attrib.get("segmentNumber", "0") or "0")
            hash_value = (segment.text or "").strip()
            if number and hash_value:
                segments.append(ValidationSegment(segment_number=number, hash=hash_value))

        return cls(
            segment_size=int(text(".//segmentSize", "0") or "0"),
            version=text(".//version"),
            algorithm=text(".//algorithm"),
            segment_count=int(text(".//segmentCount", "0") or "0"),
            last_segment_size=int(text(".//lastSegmentSize", "0") or "0"),
            package_hash_key=text(".//packageHashKey"),
            segments=segments,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_size": self.segment_size,
            "version": self.version,
            "algorithm": self.algorithm,
            "segment_count": self.segment_count,
            "last_segment_size": self.last_segment_size,
            "package_hash_key": self.package_hash_key,
            "segments": [asdict(segment) for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ValidationInfo":
        return cls(
            segment_size=payload["segment_size"],
            version=payload["version"],
            algorithm=payload["algorithm"],
            segment_count=payload["segment_count"],
            last_segment_size=payload["last_segment_size"],
            package_hash_key=payload["package_hash_key"],
            segments=[ValidationSegment(**segment) for segment in payload.get("segments", [])],
        )


@dataclass
class Package:
    type: str
    full_package_name: str
    download_size: int
    download_url: str
    package_version: str
    validation_url: str | None = None
    condition: str = ""
    is_required: bool = False
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    downloaded_size: int = 0
    progress: float = 0.0
    speed: float = 0.0
    status: PackageStatus = PackageStatus.WAITING
    downloaded: bool = False
    is_selected: bool = False
    retry_count: int = 0
    last_error: str | None = None

    def __post_init__(self) -> None:
        if self.is_required and not self.is_selected:
            self.is_selected = True

    def update_progress(self, downloaded_size: int, speed: float) -> None:
        self.downloaded_size = downloaded_size
        self.speed = speed
        self.progress = float(downloaded_size) / float(self.download_size) if self.download_size > 0 else 0.0
        self.status = PackageStatus.DOWNLOADING

    def mark_as_completed(self) -> None:
        self.downloaded = True
        self.progress = 1.0
        self.speed = 0.0
        self.status = PackageStatus.COMPLETED
        self.downloaded_size = self.download_size

    def mark_as_failed(self, error: str) -> None:
        self.last_error = error
        self.status = PackageStatus.FAILED


@dataclass
class DependenciesToDownload:
    sap_code: str
    version: str
    build_guid: str
    application_json: str = ""
    packages: list[Package] = field(default_factory=list)
    completed_packages: int = 0

    @property
    def total_packages(self) -> int:
        return len(self.packages)

    def update_completed_packages(self) -> None:
        self.completed_packages = sum(1 for package in self.packages if package.downloaded)


@dataclass(slots=True)
class ResolvedCatalog:
    secure_cdn: str
    ccm_products: list[Product]
    sti_products: list[Product]

    def find_ccm_product(self, product_id: str, version: str) -> Product | None:
        for product in self.ccm_products:
            if product.id == product_id and product.version == version:
                return product
        return None

    def find_sti_products(self, sap_code: str) -> list[Product]:
        return [product for product in self.sti_products if product.id == sap_code]


@dataclass(slots=True)
class ResolvedDownloadPlan:
    product_id: str
    product_version: str
    display_name: str
    secure_cdn: str
    dependencies: list[DependenciesToDownload]
    is_apro: bool = False


@dataclass
class DownloadTask:
    product_id: str
    product_version: str
    language: str
    display_name: str
    directory: Path
    platform: str
    dependencies_to_download: list[DependenciesToDownload] = field(default_factory=list)
    retry_count: int = 0
    created_at: datetime = field(default_factory=_utcnow)
    total_status: TaskStatus = field(default_factory=TaskStatus.waiting)
    total_progress: float = 0.0
    total_downloaded_size: int = 0
    total_size: int = 0
    total_speed: float = 0.0
    completed_packages: int = 0
    total_packages: int = 0
    current_package: Package | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    display_install_button: bool = field(init=False)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self.display_install_button = self.product_id != "APRO"

    @property
    def status(self) -> TaskStatus:
        return self.total_status

    @property
    def destination_url(self) -> Path:
        return self.directory

    def set_status(self, new_status: TaskStatus) -> None:
        self.total_status = new_status

    def update_progress(self, downloaded: int, total: int, speed: float) -> None:
        self.total_downloaded_size = downloaded
        self.total_size = total
        self.total_speed = speed
        self.total_progress = float(downloaded) / float(total) if total > 0 else 0.0


@dataclass(slots=True)
class DownloadChunk:
    index: int
    start_offset: int
    end_offset: int
    size: int
    downloaded_size: int = 0
    is_completed: bool = False
    is_paused: bool = False
    expected_hash: str | None = None

    @property
    def progress(self) -> float:
        return float(self.downloaded_size) / float(self.size) if self.size > 0 else 0.0


@dataclass(slots=True)
class ChunkedDownloadState:
    package_identifier: str
    total_size: int
    chunk_size: int
    chunks: list[DownloadChunk]
    total_downloaded_size: int
    is_completed: bool
    destination_url: str
    validation_info: ValidationInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_identifier": self.package_identifier,
            "total_size": self.total_size,
            "chunk_size": self.chunk_size,
            "chunks": [asdict(chunk) for chunk in self.chunks],
            "total_downloaded_size": self.total_downloaded_size,
            "is_completed": self.is_completed,
            "destination_url": self.destination_url,
            "validation_info": self.validation_info.to_dict() if self.validation_info else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChunkedDownloadState":
        validation_info = payload.get("validation_info")
        return cls(
            package_identifier=payload["package_identifier"],
            total_size=payload["total_size"],
            chunk_size=payload["chunk_size"],
            chunks=[DownloadChunk(**chunk) for chunk in payload.get("chunks", [])],
            total_downloaded_size=payload["total_downloaded_size"],
            is_completed=payload["is_completed"],
            destination_url=payload["destination_url"],
            validation_info=ValidationInfo.from_dict(validation_info) if validation_info else None,
        )


@dataclass(slots=True)
class HTTPResponse:
    data: bytes
    status: int
    headers: dict[str, str]


class DownloadFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "download_error",
        recoverable: bool = False,
        status_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.recoverable = recoverable
        self.status_code = status_code
        self.cause = cause

    def __str__(self) -> str:
        return self.message


class DownloadCancelled(DownloadFailure):
    def __init__(self, message: str = "Download cancelled") -> None:
        super().__init__(message, kind="cancelled", recoverable=False)


class NetworkConstants:
    DOWNLOAD_TIMEOUT = 300.0
    MAX_RETRY_ATTEMPTS = 3
    RETRY_DELAY = 3.0
    BUFFER_SIZE = 1024 * 1024
    MAX_CONCURRENT_DOWNLOADS = 3
    PROGRESS_UPDATE_INTERVAL = 1.0
    APPLICATION_JSON_URL = "https://cdn-ffc.oobesaas.adobe.com/core/v3/applications"
    DEFAULT_PROXY_URL: str | None = None

    @staticmethod
    def generate_cookie() -> str:
        alphabet = string.ascii_uppercase + string.digits
        random_part = "".join(secrets.choice(alphabet) for _ in range(26))
        return f"fg={random_part}======"

    @staticmethod
    def products_json_url(api_version: str = "6") -> str:
        return (
            "https://prod-rel-ffc-ccm.oobesaas.adobe.com/"
            f"adobe-ffc-external/core/v{api_version}/products/all"
        )

    @staticmethod
    def products_catalog_url(api_version: str = "6", platforms: str | tuple[str, ...] | list[str] | None = None) -> str:
        if platforms is None:
            platform_value = "macarm64,macuniversal,osx10-64,osx10"
        elif isinstance(platforms, str):
            platform_value = platforms
        else:
            platform_value = ",".join(platforms)
        query = urllib.parse.urlencode(
            [
                ("channel", "ccm"),
                ("channel", "sti"),
                ("platform", platform_value),
                ("_type", "json"),
                ("productType", "Desktop"),
            ]
        )
        return f"{NetworkConstants.products_json_url(api_version)}?{query}"

    @staticmethod
    def adobe_request_headers(api_version: str = "6") -> dict[str, str]:
        return {
            "x-adobe-app-id": "accc-apps-panel-desktop",
            "x-api-key": f"Creative Cloud_v{api_version}_4",
            "User-Agent": "Creative Cloud/6.4.0.361/Mac-15.1",
            "Cookie": NetworkConstants.generate_cookie(),
        }

    @staticmethod
    def download_headers() -> dict[str, str]:
        return {"User-Agent": "Creative Cloud"}


def _normalize_proxy_url(proxy_url: str | None) -> str | None:
    if proxy_url is None:
        return None
    candidate = proxy_url.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        raise DownloadFailure(f"Invalid proxy URL: {proxy_url}", kind="invalid_data", recoverable=False)
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_url_opener(proxy_url: str | None) -> urllib.request.OpenerDirector:
    normalized_proxy_url = _normalize_proxy_url(proxy_url)
    if normalized_proxy_url is None:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(
            {
                "http": normalized_proxy_url,
                "https": normalized_proxy_url,
            }
        )
    )


def _translate_exception(error: BaseException) -> DownloadFailure:
    if isinstance(error, DownloadFailure):
        return error
    if isinstance(error, asyncio.CancelledError):
        return DownloadCancelled()
    if isinstance(error, urllib.error.HTTPError):
        body = b""
        with contextlib.suppress(Exception):
            body = error.read()
        message = body.decode("utf-8", errors="ignore").strip() or str(error.reason)
        return DownloadFailure(
            f"HTTP {error.code}: {message}",
            kind="http_error",
            recoverable=500 <= error.code < 600,
            status_code=error.code,
            cause=error,
        )
    if isinstance(error, urllib.error.URLError):
        if isinstance(error.reason, TimeoutError):
            return DownloadFailure("Request timed out", kind="timeout", recoverable=True, cause=error)
        return DownloadFailure(
            f"Server unreachable: {error.reason}",
            kind="server_unreachable",
            recoverable=True,
            cause=error,
        )
    if isinstance(error, TimeoutError):
        return DownloadFailure("Request timed out", kind="timeout", recoverable=True, cause=error)
    if isinstance(error, PermissionError):
        return DownloadFailure(
            "Write permission denied",
            kind="file_permission_denied",
            recoverable=False,
            cause=error,
        )
    if isinstance(error, FileNotFoundError):
        return DownloadFailure("File not found", kind="file_not_found", recoverable=False, cause=error)
    if isinstance(error, OSError):
        return DownloadFailure(str(error), kind="file_system_error", recoverable=False, cause=error)
    return DownloadFailure(str(error) or error.__class__.__name__, kind="unknown", recoverable=False, cause=error)


def _http_request_sync(
    url: str,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    timeout: float = NetworkConstants.DOWNLOAD_TIMEOUT,
    proxy_url: str | None = NetworkConstants.DEFAULT_PROXY_URL,
) -> HTTPResponse:
    last_error: BaseException | None = None
    for attempt in range(NetworkConstants.MAX_RETRY_ATTEMPTS):
        request = urllib.request.Request(url, headers=dict(headers or {}), method=method)
        opener = _build_url_opener(proxy_url)
        try:
            with opener.open(request, timeout=timeout) as response:
                return HTTPResponse(
                    data=response.read(),
                    status=response.getcode(),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as error:
            raise _translate_exception(error) from error
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt < NetworkConstants.MAX_RETRY_ATTEMPTS - 1:
                time.sleep(NetworkConstants.RETRY_DELAY * (attempt + 1))
                continue
            raise _translate_exception(error) from error
        except BaseException as error:
            raise _translate_exception(error) from error

    if last_error is not None:
        raise _translate_exception(last_error) from last_error
    raise DownloadFailure(f"Request failed: {url}", kind="unknown", recoverable=False)


async def _http_request(
    url: str,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    timeout: float = NetworkConstants.DOWNLOAD_TIMEOUT,
    proxy_url: str | None = NetworkConstants.DEFAULT_PROXY_URL,
) -> HTTPResponse:
    return await asyncio.to_thread(_http_request_sync, url, headers, method, timeout, proxy_url)


class AsyncFlag:
    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        self._event.clear()


class CancelTracker:
    def __init__(self) -> None:
        self._cancelled_ids: set[uuid.UUID] = set()
        self._paused_ids: set[uuid.UUID] = set()
        self._download_tasks: dict[uuid.UUID, asyncio.Task[Any]] = {}
        self._task_package_identifiers: dict[uuid.UUID, str] = {}
        self._lock = threading.RLock()

    def register_task(self, task_id: uuid.UUID, task: asyncio.Task[Any], package_identifier: str = "") -> None:
        with self._lock:
            self._download_tasks[task_id] = task
            if package_identifier:
                self._task_package_identifiers[task_id] = package_identifier

    def cancel(self, task_id: uuid.UUID) -> None:
        with self._lock:
            self._cancelled_ids.add(task_id)
            self._paused_ids.discard(task_id)
            task = self._download_tasks.pop(task_id, None)
            self._task_package_identifiers.pop(task_id, None)
        if task is not None:
            task.cancel()

    def pause(self, task_id: uuid.UUID) -> None:
        with self._lock:
            if task_id in self._cancelled_ids:
                return
            self._paused_ids.add(task_id)
            task = self._download_tasks.get(task_id)
        if task is not None:
            task.cancel()

    def resume(self, task_id: uuid.UUID) -> None:
        with self._lock:
            self._paused_ids.discard(task_id)

    def is_cancelled(self, task_id: uuid.UUID) -> bool:
        with self._lock:
            return task_id in self._cancelled_ids

    def is_paused(self, task_id: uuid.UUID) -> bool:
        with self._lock:
            return task_id in self._paused_ids

    def cleanup_completed_tasks(self) -> None:
        with self._lock:
            completed_ids = [task_id for task_id, task in self._download_tasks.items() if task.done()]
            for task_id in completed_ids:
                self._download_tasks.pop(task_id, None)
                self._task_package_identifiers.pop(task_id, None)

    def get_task_package_map(self) -> dict[uuid.UUID, tuple[asyncio.Task[Any], str]]:
        with self._lock:
            return {
                task_id: (task, self._task_package_identifiers.get(task_id, ""))
                for task_id, task in self._download_tasks.items()
            }


class ConcurrentDownloadProgressManager:
    def __init__(self) -> None:
        self._package_progresses: dict[str, float] = {}
        self._package_sizes: dict[str, int] = {}
        self._package_speeds: dict[str, float] = {}
        self._total_size = 0
        self._lock = asyncio.Lock()

    async def initialize(self, packages: list[tuple[str, int]]) -> None:
        async with self._lock:
            self._total_size = sum(size for _, size in packages)
            for package_id, size in packages:
                self._package_progresses[package_id] = 0.0
                self._package_sizes[package_id] = size
                self._package_speeds[package_id] = 0.0

    async def update_package_progress(self, package_id: str, progress: float, speed: float = 0.0) -> None:
        async with self._lock:
            self._package_progresses[package_id] = progress
            self._package_speeds[package_id] = speed

    async def mark_package_completed(self, package_id: str) -> None:
        async with self._lock:
            self._package_progresses[package_id] = 1.0
            self._package_speeds[package_id] = 0.0

    async def get_total_progress(self) -> tuple[float, int, float]:
        async with self._lock:
            total_downloaded = sum(
                int(self._package_sizes.get(package_id, 0) * progress)
                for package_id, progress in self._package_progresses.items()
            )
            total_progress = float(total_downloaded) / float(self._total_size) if self._total_size > 0 else 0.0
            total_speed = sum(self._package_speeds.values())
            return total_progress, total_downloaded, total_speed

    async def is_all_completed(self) -> bool:
        async with self._lock:
            return all(progress >= 1.0 for progress in self._package_progresses.values())


class ChunkedDownloadManager:
    def __init__(
        self,
        *,
        state_directory: Path | None = None,
        chunk_size_mb: int = 2,
        proxy_url: str | None = NetworkConstants.DEFAULT_PROXY_URL,
    ) -> None:
        self.chunk_size = chunk_size_mb * 1024 * 1024
        self.state_directory = state_directory or (Path(tempfile.gettempdir()) / "adobe_downloader_chunk_states")
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self.proxy_url = _normalize_proxy_url(proxy_url)
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def check_range_support(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[bool, int, str | None]:
        response = await _http_request(url, headers=headers, method="HEAD", proxy_url=self.proxy_url)
        accepts_ranges = response.headers.get("Accept-Ranges", "").lower() == "bytes"
        total_size = int(response.headers.get("Content-Length", "0") or "0")
        etag = response.headers.get("ETag")
        return accepts_ranges, total_size, etag

    def create_chunked_download(
        self,
        total_size: int,
        validation_info: ValidationInfo | None = None,
    ) -> list[DownloadChunk]:
        if validation_info is not None:
            chunks: list[DownloadChunk] = []
            for segment in validation_info.segments:
                index = segment.segment_number - 1
                start_offset = index * validation_info.segment_size
                is_last = segment.segment_number == validation_info.segment_count
                chunk_size = validation_info.last_segment_size if is_last else validation_info.segment_size
                end_offset = start_offset + chunk_size - 1
                chunks.append(
                    DownloadChunk(
                        index=index,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        size=chunk_size,
                        expected_hash=segment.hash,
                    )
                )
            return sorted(chunks, key=lambda chunk: chunk.index)

        standard_chunk_size = 2 * 1024 * 1024
        chunk_count = max(1, (total_size + standard_chunk_size - 1) // standard_chunk_size)
        chunks = []
        for index in range(chunk_count):
            start_offset = index * standard_chunk_size
            is_last = index == chunk_count - 1
            chunk_size = total_size - start_offset if is_last else standard_chunk_size
            end_offset = start_offset + chunk_size - 1
            chunks.append(
                DownloadChunk(
                    index=index,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    size=chunk_size,
                )
            )
        return chunks

    def _state_file(self, package_identifier: str) -> Path:
        safe_name = package_identifier.replace("/", "_").replace("\\", "_")
        return self.state_directory / f"{safe_name}.chunkstate"

    def save_chunked_download_state(
        self,
        package_identifier: str,
        chunks: list[DownloadChunk],
        total_size: int,
        destination_url: Path,
        validation_info: ValidationInfo | None = None,
    ) -> None:
        state = ChunkedDownloadState(
            package_identifier=package_identifier,
            total_size=total_size,
            chunk_size=validation_info.segment_size if validation_info is not None else self.chunk_size,
            chunks=chunks,
            total_downloaded_size=sum(chunk.downloaded_size for chunk in chunks),
            is_completed=all(chunk.is_completed for chunk in chunks),
            destination_url=str(destination_url),
            validation_info=validation_info,
        )
        self._state_file(package_identifier).write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")

    def load_chunked_download_state(self, package_identifier: str) -> ChunkedDownloadState | None:
        file_path = self._state_file(package_identifier)
        if not file_path.exists():
            return None
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return ChunkedDownloadState.from_dict(payload)

    def restore_chunks_from_state(self, state: ChunkedDownloadState) -> list[DownloadChunk]:
        return [
            DownloadChunk(
                index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                size=chunk.size,
                downloaded_size=chunk.downloaded_size,
                is_completed=chunk.is_completed,
                is_paused=chunk.is_paused,
                expected_hash=chunk.expected_hash,
            )
            for chunk in state.chunks
        ]

    def clear_chunked_download_state(self, package_identifier: str) -> None:
        self._state_file(package_identifier).unlink(missing_ok=True)

    async def fetch_validation_info(self, validation_url: str) -> ValidationInfo | None:
        response = await _http_request(validation_url, proxy_url=self.proxy_url)
        xml_text = response.data.decode("utf-8", errors="ignore")
        validation_info = ValidationInfo.parse(xml_text)
        if validation_info is None:
            raise DownloadFailure("Failed to parse validation xml", kind="invalid_data", recoverable=False)
        return validation_info

    def validate_chunk_hash(self, data: bytes, expected_hash: str) -> bool:
        actual_hash = hashlib.md5(data).hexdigest()
        return actual_hash.lower() == expected_hash.lower()

    def validate_complete_chunk_from_file(self, destination_url: Path, chunk: DownloadChunk) -> bool:
        if chunk.expected_hash is None:
            return True
        if not destination_url.exists():
            return False
        try:
            with destination_url.open("rb") as handle:
                handle.seek(chunk.start_offset)
                chunk_data = handle.read(chunk.size)
        except OSError:
            return False
        return self.validate_chunk_hash(chunk_data, chunk.expected_hash)

    async def ensure_file_preallocated(self, destination_url: Path, total_size: int) -> None:
        def worker() -> None:
            destination_url.parent.mkdir(parents=True, exist_ok=True)
            if destination_url.exists():
                existing_size = destination_url.stat().st_size
                if existing_size == total_size:
                    return
                if existing_size > total_size:
                    destination_url.unlink(missing_ok=True)
            mode = "r+b" if destination_url.exists() else "w+b"
            with destination_url.open(mode) as handle:
                handle.truncate(total_size)

        try:
            await asyncio.to_thread(worker)
        except BaseException as error:
            raise _translate_exception(error) from error

    async def _write_data_to_file(self, data: bytes, destination_url: Path, offset: int) -> None:
        def worker() -> None:
            destination_url.parent.mkdir(parents=True, exist_ok=True)
            mode = "r+b" if destination_url.exists() else "w+b"
            with destination_url.open(mode) as handle:
                handle.seek(offset)
                handle.write(data)
                handle.flush()

        try:
            await asyncio.to_thread(worker)
        except BaseException as error:
            raise _translate_exception(error) from error

    async def download_chunk_to_file(
        self,
        chunk: DownloadChunk,
        url: str,
        destination_url: Path,
        headers: Mapping[str, str] | None = None,
        cancellation_handler: Callback | None = None,
    ) -> DownloadChunk:
        modified_chunk = DownloadChunk(
            index=chunk.index,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            size=chunk.size,
            downloaded_size=chunk.downloaded_size,
            is_completed=chunk.is_completed,
            is_paused=chunk.is_paused,
            expected_hash=chunk.expected_hash,
        )

        if cancellation_handler and bool(cancellation_handler()):
            modified_chunk.is_paused = True
            raise DownloadCancelled()

        if destination_url.exists():
            if chunk.expected_hash is not None:
                if self.validate_complete_chunk_from_file(destination_url, chunk):
                    modified_chunk.downloaded_size = chunk.size
                    modified_chunk.is_completed = True
                    return modified_chunk
                modified_chunk.downloaded_size = 0
            else:
                existing_size = destination_url.stat().st_size
                if existing_size > chunk.end_offset:
                    with destination_url.open("rb") as handle:
                        handle.seek(chunk.start_offset)
                        data = handle.read(chunk.size)
                    if data and any(byte != 0 for byte in data):
                        modified_chunk.downloaded_size = chunk.size
                        modified_chunk.is_completed = True
                        return modified_chunk

        if modified_chunk.downloaded_size >= chunk.size:
            modified_chunk.is_completed = True
            return modified_chunk

        actual_start_offset = chunk.start_offset + modified_chunk.downloaded_size
        request_headers = dict(headers or {})
        request_headers["Range"] = f"bytes={actual_start_offset}-{chunk.end_offset}"
        response = await _http_request(url, headers=request_headers, proxy_url=self.proxy_url)

        if response.status not in (200, 206):
            raise DownloadFailure(
                f"Chunk download failed with HTTP {response.status}",
                kind="http_error",
                recoverable=500 <= response.status < 600,
                status_code=response.status,
            )

        if cancellation_handler and bool(cancellation_handler()):
            modified_chunk.is_paused = True
            raise DownloadCancelled()

        await self._write_data_to_file(response.data, destination_url, actual_start_offset)
        modified_chunk.downloaded_size += len(response.data)
        if modified_chunk.downloaded_size >= chunk.size:
            modified_chunk.is_completed = True
            if chunk.expected_hash is not None and not self.validate_complete_chunk_from_file(destination_url, chunk):
                raise DownloadFailure(
                    f"Chunk hash validation failed: {chunk.index}",
                    kind="invalid_data",
                    recoverable=False,
                )
        return modified_chunk

    async def _download_chunks_sequentially(
        self,
        all_chunks: list[DownloadChunk],
        incomplete_chunks: list[DownloadChunk],
        url: str,
        destination_url: Path,
        headers: Mapping[str, str] | None,
        progress_handler: Callback | None,
        cancellation_handler: Callback | None,
        package_identifier: str,
        total_size: int,
        validation_info: ValidationInfo | None,
    ) -> None:
        current_chunks = {chunk.index: chunk for chunk in all_chunks}
        last_progress_time = time.monotonic()
        last_downloaded_size = sum(chunk.downloaded_size for chunk in current_chunks.values())

        for chunk in sorted(incomplete_chunks, key=lambda item: item.index):
            if cancellation_handler and bool(cancellation_handler()):
                raise DownloadCancelled()

            completed_chunk = await self.download_chunk_to_file(
                chunk=chunk,
                url=url,
                destination_url=destination_url,
                headers=headers,
                cancellation_handler=cancellation_handler,
            )
            current_chunks[completed_chunk.index] = completed_chunk
            ordered_chunks = [current_chunks[index] for index in sorted(current_chunks)]
            self.save_chunked_download_state(
                package_identifier=package_identifier,
                chunks=ordered_chunks,
                total_size=total_size,
                destination_url=destination_url,
                validation_info=validation_info,
            )

            current_total_downloaded = sum(item.downloaded_size for item in ordered_chunks)
            now = time.monotonic()
            elapsed = max(now - last_progress_time, 1e-9)
            speed = float(current_total_downloaded - last_downloaded_size) / elapsed
            progress = float(current_total_downloaded) / float(total_size) if total_size > 0 else 0.0

            await _invoke_callback(progress_handler, progress, current_total_downloaded, total_size, speed)

            last_progress_time = now
            last_downloaded_size = current_total_downloaded

    async def validate_complete_file(self, destination_url: Path, validation_info: ValidationInfo, total_size: int) -> None:
        if not destination_url.exists():
            raise DownloadFailure("Downloaded file does not exist", kind="file_not_found", recoverable=False)

        actual_size = destination_url.stat().st_size
        if actual_size != total_size:
            raise DownloadFailure(
                f"File size mismatch: expected {total_size}, got {actual_size}",
                kind="invalid_data",
                recoverable=False,
            )

        with destination_url.open("rb") as handle:
            for segment in validation_info.segments:
                segment_index = segment.segment_number - 1
                start_offset = segment_index * validation_info.segment_size
                is_last = segment.segment_number == validation_info.segment_count
                segment_size = validation_info.last_segment_size if is_last else validation_info.segment_size

                handle.seek(start_offset)
                segment_data = handle.read(segment_size)
                if len(segment_data) != segment_size:
                    raise DownloadFailure(
                        f"Segment size mismatch: expected {segment_size}, got {len(segment_data)}",
                        kind="invalid_data",
                        recoverable=False,
                    )
                if not self.validate_chunk_hash(segment_data, segment.hash):
                    raise DownloadFailure(
                        f"Segment hash validation failed: {segment.segment_number}",
                        kind="invalid_data",
                        recoverable=False,
                    )

    async def _download_file_with_chunks_impl(
        self,
        package_identifier: str,
        url: str,
        destination_url: Path,
        headers: Mapping[str, str] | None = None,
        validation_url: str | None = None,
        progress_handler: Callback | None = None,
        cancellation_handler: Callback | None = None,
    ) -> None:
        supports_range, total_size, _ = await self.check_range_support(url=url, headers=headers)
        if total_size <= 0:
            raise DownloadFailure("Unable to determine file size", kind="invalid_data", recoverable=False)

        validation_info: ValidationInfo | None = None
        if validation_url:
            with contextlib.suppress(DownloadFailure):
                validation_info = await self.fetch_validation_info(validation_url)

        current_chunk_size = validation_info.segment_size if validation_info is not None else self.chunk_size
        saved_state = self.load_chunked_download_state(package_identifier)

        if (
            saved_state is not None
            and saved_state.total_size == total_size
            and saved_state.destination_url == str(destination_url)
            and saved_state.package_identifier == package_identifier
            and saved_state.chunk_size == current_chunk_size
        ):
            chunks = self.restore_chunks_from_state(saved_state)
            for chunk in chunks:
                chunk.is_paused = False
        else:
            if saved_state is not None:
                self.clear_chunked_download_state(package_identifier)
            if supports_range and (validation_info is not None or total_size > self.chunk_size):
                chunks = self.create_chunked_download(total_size=total_size, validation_info=validation_info)
            else:
                chunks = [
                    DownloadChunk(
                        index=0,
                        start_offset=0,
                        end_offset=total_size - 1,
                        size=total_size,
                    )
                ]

        incomplete_chunks: list[DownloadChunk] = []
        for chunk in chunks:
            if chunk.is_paused or chunk.is_completed or chunk.downloaded_size >= chunk.size:
                continue
            if chunk.expected_hash is not None and destination_url.exists() and self.validate_complete_chunk_from_file(destination_url, chunk):
                chunk.downloaded_size = chunk.size
                chunk.is_completed = True
                continue
            incomplete_chunks.append(chunk)

        if not incomplete_chunks:
            self.clear_chunked_download_state(package_identifier)
            await _invoke_callback(progress_handler, 1.0, total_size, total_size, 0.0)
            return

        await self.ensure_file_preallocated(destination_url, total_size)
        await self._download_chunks_sequentially(
            all_chunks=chunks,
            incomplete_chunks=incomplete_chunks,
            url=url,
            destination_url=destination_url,
            headers=headers,
            progress_handler=progress_handler,
            cancellation_handler=cancellation_handler,
            package_identifier=package_identifier,
            total_size=total_size,
            validation_info=validation_info,
        )

        if validation_info is not None:
            await self.validate_complete_file(destination_url, validation_info, total_size)

        self.clear_chunked_download_state(package_identifier)
        await _invoke_callback(progress_handler, 1.0, total_size, total_size, 0.0)

    async def download_file_with_chunks(
        self,
        package_identifier: str,
        url: str,
        destination_url: Path,
        headers: Mapping[str, str] | None = None,
        validation_url: str | None = None,
        progress_handler: Callback | None = None,
        cancellation_handler: Callback | None = None,
    ) -> None:
        download_task = asyncio.create_task(
            self._download_file_with_chunks_impl(
                package_identifier=package_identifier,
                url=url,
                destination_url=destination_url,
                headers=headers,
                validation_url=validation_url,
                progress_handler=progress_handler,
                cancellation_handler=cancellation_handler,
            )
        )

        async with self._lock:
            self._active_tasks[package_identifier] = download_task

        try:
            await download_task
        finally:
            async with self._lock:
                self._active_tasks.pop(package_identifier, None)

    async def pause_download(self, package_identifier: str) -> None:
        async with self._lock:
            task = self._active_tasks.pop(package_identifier, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def cancel_download(self, package_identifier: str) -> None:
        async with self._lock:
            task = self._active_tasks.pop(package_identifier, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        self.clear_chunked_download_state(package_identifier)


class AdobeDownloadManager:
    def __init__(
        self,
        *,
        cdn: str = "",
        products: list[Product] | None = None,
        api_version: str = "6",
        default_language: str = "ALL",
        download_apple_silicon: bool = True,
        target_platform: str | None = None,
        current_os_version: float | None = None,
        proxy_url: str | None = NetworkConstants.DEFAULT_PROXY_URL,
        max_concurrent_downloads: int = NetworkConstants.MAX_CONCURRENT_DOWNLOADS,
        cancel_tracker: CancelTracker | None = None,
        chunked_downloader: ChunkedDownloadManager | None = None,
        save_task: Callback | None = None,
        setup_modifier: Callback | None = None,
        cc_target_directory: Path | None = None,
    ) -> None:
        self.cdn = cdn
        self.products = products or []
        self.api_version = api_version
        self.default_language = default_language
        self.download_apple_silicon = download_apple_silicon
        self.target_platform = self._resolve_target_platform(target_platform, download_apple_silicon)
        self.current_os_version = current_os_version if current_os_version is not None else self._detect_current_os_version()
        self.proxy_url = _normalize_proxy_url(proxy_url)
        self.max_concurrent_downloads = max_concurrent_downloads
        self.cancel_tracker = cancel_tracker or CancelTracker()
        self.chunked_downloader = chunked_downloader or ChunkedDownloadManager(proxy_url=self.proxy_url)
        if hasattr(self.chunked_downloader, "proxy_url"):
            self.chunked_downloader.proxy_url = self.proxy_url
        self.save_task = save_task
        self.setup_modifier = setup_modifier
        self.cc_target_directory = cc_target_directory or self._default_cc_target_directory()
        self.tasks: dict[uuid.UUID, DownloadTask] = {}

    @staticmethod
    def _normalize_platform_name(platform_name: str | None) -> str:
        normalized = (platform_name or "").strip().lower()
        aliases = {
            "mac-x64": "osx10-64",
            "mac_x64": "osx10-64",
            "macintel": "osx10-64",
            "mac-intel": "osx10-64",
            "windows-x64": "win64",
            "windows_x64": "win64",
            "windows-arm64": "winarm64",
            "windows_arm64": "winarm64",
            "windows-x86": "win32",
            "windows_x86": "win32",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _resolve_target_platform(cls, target_platform: str | None, download_apple_silicon: bool) -> str:
        normalized = cls._normalize_platform_name(target_platform)
        if normalized:
            supported_platforms = {
                "macarm64",
                "macuniversal",
                "osx10-64",
                "osx10",
                "win64",
                "winarm64",
                "win32",
            }
            if normalized not in supported_platforms:
                raise DownloadFailure(
                    f"Unsupported target platform: {target_platform}",
                    kind="invalid_data",
                    recoverable=False,
                )
            return normalized

        system_name = py_platform.system().lower()
        machine_name = py_platform.machine().lower()
        if system_name == "windows":
            if machine_name in {"arm64", "aarch64"}:
                return "winarm64"
            if machine_name in {"x86", "i386", "i686"}:
                return "win32"
            return "win64"
        return "macarm64" if download_apple_silicon else "osx10-64"

    def _platform_preference_order(self, preferred_platform: str | None = None) -> tuple[str, ...]:
        target_platform = self._normalize_platform_name(preferred_platform) or self.target_platform
        preference_map = {
            "macarm64": ("macarm64", "macuniversal", "osx10-64", "osx10"),
            "macuniversal": ("macuniversal", "macarm64", "osx10-64", "osx10"),
            "osx10-64": ("osx10-64", "osx10", "macuniversal", "macarm64"),
            "osx10": ("osx10", "osx10-64", "macuniversal", "macarm64"),
            "win64": ("win64", "win32"),
            "winarm64": ("winarm64", "win64", "win32"),
            "win32": ("win32", "win64"),
        }
        return preference_map.get(target_platform, (target_platform,))

    def _catalog_platforms(self) -> tuple[str, ...]:
        return self._platform_preference_order()

    def _select_platform(self, platforms: list[Platform], preferred_platform: str | None = None) -> Platform | None:
        ordered_platforms = [platform for platform in platforms if platform.language_sets]
        if not ordered_platforms:
            return None

        platform_map = {platform.id: platform for platform in ordered_platforms}
        for platform_id in self._platform_preference_order(preferred_platform):
            if platform_id in platform_map:
                return platform_map[platform_id]
        return ordered_platforms[0]

    def _is_windows_target(self, platform_id: str | None = None) -> bool:
        normalized = self._normalize_platform_name(platform_id) or self.target_platform
        return normalized.startswith("win")

    def _target_architecture_aliases(self) -> tuple[str, ...]:
        architecture_map = {
            "macarm64": ("arm64", "aarch64"),
            "macuniversal": ("arm64", "aarch64", "x64", "amd64", "64-bit"),
            "osx10-64": ("x64", "amd64", "64-bit"),
            "osx10": ("x64", "amd64", "64-bit"),
            "win64": ("x64", "amd64", "win64", "64-bit"),
            "winarm64": ("arm64", "aarch64", "winarm64"),
            "win32": ("x86", "win32", "32-bit", "i386"),
        }
        return architecture_map.get(self.target_platform, (self.target_platform,))

    def _matches_target_architecture(self, condition: str) -> bool:
        return any(f"[OSArchitecture]=={alias}" in condition for alias in self._target_architecture_aliases())

    def _default_install_directory(self) -> str:
        if self._is_windows_target():
            return r"C:\Program Files\Adobe"
        return "/Applications"

    def _default_cc_target_directory(self) -> Path:
        if self._is_windows_target():
            return Path("C:/Program Files/Common Files/Adobe/Adobe Desktop Common")
        return Path("/Library/Application Support/Adobe/Adobe Desktop Common")

    def _creative_cloud_platform_name(self) -> str:
        if self.target_platform in {"macarm64", "macuniversal"}:
            return "macarm64"
        if self.target_platform in {"osx10-64", "osx10"}:
            return "osx10"
        return self.target_platform

    def _creative_cloud_helper_targets(self) -> tuple[tuple[str, str], ...]:
        if self._is_windows_target():
            return (("ADC", "HDBox"), ("AAM", "IPC"))
        return (("ADC", "HDBox"), ("ADC", "IPCBox"))

    @staticmethod
    def _package_name_from_download_url(download_url: str, fallback_name: str) -> str:
        package_name = Path(urllib.parse.urlparse(download_url).path).name
        return package_name or fallback_name

    @staticmethod
    def _package_type_from_download_url(download_url: str, default_type: str = "package") -> str:
        suffix = Path(urllib.parse.urlparse(download_url).path).suffix.lower().lstrip(".")
        return suffix or default_type

    def register_task(self, task: DownloadTask) -> None:
        self.tasks[task.id] = task

    def unregister_task(self, task_id: uuid.UUID) -> None:
        self.tasks.pop(task_id, None)

    async def _persist_task(self, task: DownloadTask) -> None:
        self.tasks[task.id] = task
        await _invoke_callback(self.save_task, task)

    def _find_product(self, product_id: str, version: str) -> Product | None:
        for product in self.products:
            if product.id == product_id and product.version == version:
                return product
        return None

    def _normalize_download_url(self, download_url: str) -> str:
        parsed = urllib.parse.urlparse(download_url)
        if parsed.scheme and parsed.netloc:
            return download_url
        clean_cdn = self.cdn.rstrip("/")
        clean_path = download_url if download_url.startswith("/") else f"/{download_url}"
        return f"{clean_cdn}{clean_path}"

    def _detect_current_os_version(self) -> float:
        if self._is_windows_target():
            version_text = py_platform.win32_ver()[1] or "10.0"
        else:
            version_text = py_platform.mac_ver()[0]
        if not version_text:
            return 0.0
        parts = version_text.split(".")
        major = parts[0] if len(parts) > 0 else "0"
        minor = parts[1] if len(parts) > 1 else "0"
        try:
            return float(f"{major}.{minor}")
        except ValueError:
            return 0.0

    def _find_dependency_for_package(self, task: DownloadTask, package: Package) -> DependenciesToDownload | None:
        for dependency in task.dependencies_to_download:
            for candidate in dependency.packages:
                if candidate.id == package.id:
                    return dependency
        return None

    def _is_pause_related(self, error: BaseException) -> bool:
        translated = _translate_exception(error)
        return translated.kind == "cancelled"

    def _process_application_json(self, dependency: DependenciesToDownload) -> str:
        raw_json = dependency.application_json or ""
        if not raw_json.strip():
            return raw_json

        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return raw_json

        selected_package_names = {package.full_package_name for package in dependency.packages if package.is_selected}
        selected_names_without_zip = {
            package.full_package_name[:-4] if package.full_package_name.endswith(".zip") else package.full_package_name
            for package in dependency.packages
            if package.is_selected
        }

        packages_block = payload.get("Packages")
        if isinstance(packages_block, dict):
            package_array = packages_block.get("Package", [])
            if isinstance(package_array, list):
                filtered_packages = []
                for package in package_array:
                    if not isinstance(package, dict):
                        continue
                    package_name = package.get("PackageName")
                    full_name = package.get("fullPackageName")
                    if isinstance(package_name, str):
                        normalized = package_name if package_name.endswith(".zip") else f"{package_name}.zip"
                        if normalized in selected_package_names:
                            filtered_packages.append(package)
                            continue
                    if isinstance(full_name, str) and full_name in selected_package_names:
                        filtered_packages.append(package)
                packages_block["Package"] = filtered_packages
                payload["Packages"] = packages_block

        modules_block = payload.get("Modules")
        if isinstance(modules_block, dict):
            module_array = modules_block.get("Module", [])
            if isinstance(module_array, list):
                filtered_modules = []
                for module in module_array:
                    if not isinstance(module, dict):
                        continue
                    reference_packages = module.get("ReferencePackages")
                    if not isinstance(reference_packages, dict):
                        continue
                    references = reference_packages.get("ReferencePackage", [])
                    if isinstance(references, list) and any(reference in selected_names_without_zip for reference in references):
                        filtered_modules.append(module)
                modules_block["Module"] = filtered_modules
                payload["Modules"] = modules_block

        return json.dumps(payload, indent=2, ensure_ascii=True)

    async def handle_custom_download(
        self,
        task: DownloadTask,
        custom_dependencies: list[DependenciesToDownload],
    ) -> None:
        self.register_task(task)
        task.set_status(
            TaskStatus(
                "preparing",
                PrepareInfo(
                    message="Preparing custom download...",
                    timestamp=_utcnow(),
                    stage=PrepareStage.FETCHING_INFO,
                ),
            )
        )

        for dependency in custom_dependencies:
            product_dir = task.directory / dependency.sap_code
            product_dir.mkdir(parents=True, exist_ok=True)

            if dependency.application_json:
                processed_json = self._process_application_json(dependency)
                (product_dir / "application.json").write_text(processed_json, encoding="utf-8")

        filtered_dependencies: list[DependenciesToDownload] = []
        for dependency in custom_dependencies:
            selected_packages = [package for package in dependency.packages if package.is_selected]
            if not selected_packages:
                continue
            filtered_dependencies.append(
                DependenciesToDownload(
                    sap_code=dependency.sap_code,
                    version=dependency.version,
                    build_guid=dependency.build_guid,
                    application_json=dependency.application_json or "",
                    packages=selected_packages,
                )
            )

        total_size = sum(
            package.download_size
            for dependency in filtered_dependencies
            for package in dependency.packages
            if package.download_size > 0
        )

        task.dependencies_to_download = filtered_dependencies
        task.total_size = total_size

        await self.start_concurrent_download_process(task)

    async def start_concurrent_download_process(self, task: DownloadTask) -> None:
        progress_manager = ConcurrentDownloadProgressManager()
        if self.cancel_tracker.is_cancelled(task.id) or self.cancel_tracker.is_paused(task.id):
            return

        all_packages: list[tuple[Package, DependenciesToDownload, int]] = []
        current_index = 0
        for dependency in task.dependencies_to_download:
            for package in dependency.packages:
                if package.downloaded:
                    continue
                if package.status == PackageStatus.PAUSED:
                    package.status = PackageStatus.WAITING
                all_packages.append((package, dependency, current_index))
                current_index += 1

        all_packages.sort(key=lambda item: item[2])

        if not all_packages:
            task.set_status(
                TaskStatus(
                    "completed",
                    CompletionInfo(
                        timestamp=_utcnow(),
                        total_time=(_utcnow() - task.created_at).total_seconds(),
                        total_size=task.total_size,
                    ),
                )
            )
            await self._persist_task(task)
            return

        all_task_packages = [package for dependency in task.dependencies_to_download for package in dependency.packages]
        await progress_manager.initialize([(package.full_package_name, package.download_size) for package in all_task_packages])

        for package in all_task_packages:
            if package.downloaded:
                await progress_manager.update_package_progress(package.full_package_name, 1.0, 0.0)
            else:
                await progress_manager.update_package_progress(package.full_package_name, package.progress, package.speed)

        task.total_packages = len(all_packages)
        task.current_package = all_packages[0][0]
        task.set_status(
            TaskStatus(
                "downloading",
                DownloadInfo(
                    file_name=all_packages[0][0].full_package_name,
                    current_package_index=0,
                    total_packages=len(all_packages),
                    start_time=_utcnow(),
                    estimated_time_remaining=None,
                ),
            )
        )

        await self.update_task_progress(task, progress_manager)
        await self.prepare_download_environment(task)

        semaphore = asyncio.Semaphore(self.max_concurrent_downloads)
        cancel_flag = AsyncFlag()
        worker_tasks: list[asyncio.Task[Any]] = []

        async def worker(index: int, package: Package, dependency: DependenciesToDownload) -> None:
            async with semaphore:
                if cancel_flag.is_set():
                    return

                if self.cancel_tracker.is_cancelled(task.id) or self.cancel_tracker.is_paused(task.id):
                    cancel_flag.set()
                    package.status = PackageStatus.PAUSED if self.cancel_tracker.is_paused(task.id) else PackageStatus.WAITING
                    return

                package.status = PackageStatus.DOWNLOADING
                task.current_package = package
                task.set_status(
                    TaskStatus(
                        "downloading",
                        DownloadInfo(
                            file_name=package.full_package_name,
                            current_package_index=index,
                            total_packages=len(all_packages),
                            start_time=_utcnow(),
                            estimated_time_remaining=None,
                        ),
                    )
                )

                try:
                    await self.download_package_with_progress(
                        package=package,
                        task=task,
                        product=dependency,
                        progress_manager=progress_manager,
                        cancel_flag=cancel_flag,
                    )
                    await progress_manager.mark_package_completed(package.full_package_name)
                    dependency.update_completed_packages()
                    await self.update_task_progress(task, progress_manager)
                    await self._persist_task(task)
                except BaseException as error:
                    translated = _translate_exception(error)
                    if self._is_pause_related(translated):
                        cancel_flag.set()
                        package.status = PackageStatus.PAUSED
                        return
                    package.mark_as_failed(str(translated))
                    cancel_flag.set()
                    raise translated

        try:
            for index, (package, dependency, _) in enumerate(all_packages):
                worker_tasks.append(asyncio.create_task(worker(index, package, dependency)))
            await asyncio.gather(*worker_tasks)

            if await progress_manager.is_all_completed():
                task.set_status(
                    TaskStatus(
                        "completed",
                        CompletionInfo(
                            timestamp=_utcnow(),
                            total_time=(_utcnow() - task.created_at).total_seconds(),
                            total_size=task.total_size,
                        ),
                    )
                )
                await self._persist_task(task)
        except BaseException as error:
            cancel_flag.set()
            for worker_task in worker_tasks:
                if not worker_task.done():
                    worker_task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            if not self._is_pause_related(error):
                await self.handle_error(task.id, error)

    async def prepare_download_environment(self, task: DownloadTask) -> None:
        driver_path = task.directory / "driver.xml"
        if not driver_path.exists():
            product_info = self._find_product(task.product_id, task.product_version)
            if product_info is not None:
                selected_modules: list[dict[str, Any]] = []
                main_dependency = next((item for item in task.dependencies_to_download if item.sap_code == task.product_id), None)
                if main_dependency is not None:
                    json_path = task.directory / main_dependency.sap_code / "application.json"
                    if json_path.exists():
                        try:
                            payload = json.loads(json_path.read_text(encoding="utf-8"))
                            modules_block = payload.get("Modules", {})
                            module_array = modules_block.get("Module", []) if isinstance(modules_block, dict) else []
                            if isinstance(module_array, list):
                                selected_modules = [module for module in module_array if isinstance(module, dict)]
                        except json.JSONDecodeError:
                            selected_modules = []

                driver_xml = self.generate_driver_xml(
                    version=task.product_version,
                    language=task.language,
                    product_info=product_info,
                    display_name=task.display_name,
                    platform_id=task.platform,
                    modules=selected_modules,
                )
                if driver_xml:
                    try:
                        driver_path.write_text(driver_xml, encoding="utf-8")
                    except OSError as error:
                        task.set_status(
                            TaskStatus(
                                "failed",
                                FailureInfo(
                                    message=f"Failed to generate driver.xml: {error}",
                                    error=error,
                                    timestamp=_utcnow(),
                                    recoverable=False,
                                ),
                            )
                        )
                        return

        for dependency in task.dependencies_to_download:
            (task.directory / dependency.sap_code).mkdir(parents=True, exist_ok=True)

    def generate_package_identifier(
        self,
        package: Package,
        task: DownloadTask,
        dependency: DependenciesToDownload,
    ) -> str:
        stable_id = f"{task.product_id}_{task.product_version}_{dependency.sap_code}_{package.full_package_name}"
        stable_hash = hashlib.sha1(stable_id.encode("utf-8")).hexdigest()[:16]
        return f"adobe_downloader_{stable_hash}_{package.full_package_name}"

    async def download_package_with_progress(
        self,
        *,
        package: Package,
        task: DownloadTask,
        product: DependenciesToDownload,
        progress_manager: ConcurrentDownloadProgressManager,
        cancel_flag: AsyncFlag,
    ) -> None:
        if not package.full_package_name or not package.download_url or package.download_size <= 0:
            return

        download_url = self._normalize_download_url(package.download_url)
        package_identifier = self.generate_package_identifier(package, task, product)
        destination_url = task.directory / product.sap_code / package.full_package_name

        async def on_progress(progress: float, downloaded_size: int, total_size: int, speed: float) -> None:
            package.downloaded_size = downloaded_size
            package.progress = progress
            package.speed = speed
            package.status = PackageStatus.DOWNLOADING

            await progress_manager.update_package_progress(package.full_package_name, progress, speed)
            await self.update_task_progress(task, progress_manager)

        def should_cancel() -> bool:
            return (
                self.cancel_tracker.is_cancelled(task.id)
                or self.cancel_tracker.is_paused(task.id)
                or cancel_flag.is_set()
            )

        await self.chunked_downloader.download_file_with_chunks(
            package_identifier=package_identifier,
            url=download_url,
            destination_url=destination_url,
            headers=NetworkConstants.download_headers(),
            validation_url=package.validation_url,
            progress_handler=on_progress,
            cancellation_handler=should_cancel,
        )

        package.downloaded_size = package.download_size
        package.progress = 1.0
        package.status = PackageStatus.COMPLETED
        package.downloaded = True

    async def update_task_progress(
        self,
        task: DownloadTask,
        progress_manager: ConcurrentDownloadProgressManager,
    ) -> None:
        progress, downloaded_size, total_speed = await progress_manager.get_total_progress()
        task.total_downloaded_size = downloaded_size
        task.total_progress = progress
        task.total_speed = total_speed

        all_packages = [package for dependency in task.dependencies_to_download for package in dependency.packages]
        task.total_packages = len(all_packages)
        task.completed_packages = sum(1 for package in all_packages if package.downloaded)

        await self._persist_task(task)

    async def start_download_process(self, task: DownloadTask) -> None:
        self.register_task(task)
        total_packages = sum(len(dependency.packages) for dependency in task.dependencies_to_download)
        task.set_status(
            TaskStatus(
                "downloading",
                DownloadInfo(
                    file_name=task.current_package.full_package_name if task.current_package else "",
                    current_package_index=0,
                    total_packages=total_packages,
                    start_time=_utcnow(),
                    estimated_time_remaining=None,
                ),
            )
        )

        await self.prepare_download_environment(task)

        current_index = 0
        for dependency in task.dependencies_to_download:
            for package in dependency.packages:
                if package.downloaded:
                    continue

                task.current_package = package
                task.set_status(
                    TaskStatus(
                        "downloading",
                        DownloadInfo(
                            file_name=package.full_package_name,
                            current_package_index=current_index,
                            total_packages=total_packages,
                            start_time=_utcnow(),
                            estimated_time_remaining=None,
                        ),
                    )
                )
                await self._persist_task(task)
                current_index += 1

                if not package.full_package_name or not package.download_url or package.download_size <= 0:
                    continue

                try:
                    await self.download_package_direct(package=package, task=task, product=dependency)
                except BaseException as error:
                    if self._is_pause_related(error):
                        return
                    await self.handle_error(task.id, error)
                    return

        all_packages_downloaded = all(
            package.downloaded
            for dependency in task.dependencies_to_download
            for package in dependency.packages
        )
        if all_packages_downloaded:
            task.set_status(
                TaskStatus(
                    "completed",
                    CompletionInfo(
                        timestamp=_utcnow(),
                        total_time=(_utcnow() - task.created_at).total_seconds(),
                        total_size=task.total_size,
                    ),
                )
            )
            await self._persist_task(task)

    async def _stream_download(
        self,
        *,
        url: str,
        destination_url: Path,
        headers: Mapping[str, str] | None = None,
        progress_handler: Callback | None = None,
        cancellation_handler: Callback | None = None,
    ) -> tuple[int, int]:
        loop = asyncio.get_running_loop()
        partial_path = destination_url.with_suffix(f"{destination_url.suffix}.part")

        def worker() -> tuple[int, int]:
            destination_url.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
            opener = _build_url_opener(self.proxy_url)
            last_update_time = time.monotonic()
            last_bytes = 0
            total_written = 0
            total_expected = 0

            try:
                with opener.open(request, timeout=NetworkConstants.DOWNLOAD_TIMEOUT) as response:
                    total_expected = int(response.headers.get("Content-Length", "0") or "0")
                    with partial_path.open("wb") as handle:
                        while True:
                            if cancellation_handler and bool(cancellation_handler()):
                                raise DownloadCancelled()

                            chunk = response.read(NetworkConstants.BUFFER_SIZE)
                            if not chunk:
                                break

                            handle.write(chunk)
                            total_written += len(chunk)
                            now = time.monotonic()
                            if now - last_update_time >= NetworkConstants.PROGRESS_UPDATE_INTERVAL:
                                speed = float(total_written - last_bytes) / max(now - last_update_time, 1e-9)
                                asyncio.run_coroutine_threadsafe(
                                    _invoke_callback(
                                        progress_handler,
                                        len(chunk),
                                        total_written,
                                        total_expected or total_written,
                                        speed,
                                    ),
                                    loop,
                                )
                                last_update_time = now
                                last_bytes = total_written
            except BaseException as error:
                partial_path.unlink(missing_ok=True)
                raise _translate_exception(error) from error

            if destination_url.exists():
                destination_url.unlink(missing_ok=True)
            partial_path.replace(destination_url)
            asyncio.run_coroutine_threadsafe(
                _invoke_callback(progress_handler, 0, total_written, total_expected or total_written, 0.0),
                loop,
            )
            return total_written, total_expected or total_written

        try:
            return await asyncio.to_thread(worker)
        except BaseException as error:
            raise _translate_exception(error) from error

    async def download_package_direct(
        self,
        *,
        package: Package,
        task: DownloadTask,
        product: DependenciesToDownload,
        url: str | None = None,
        destination_directory: Path | None = None,
    ) -> None:
        target_url = url or self._normalize_download_url(package.download_url)
        destination_directory = destination_directory or (task.directory / product.sap_code)
        destination_url = destination_directory / package.full_package_name

        async def on_progress(_: int, total_bytes_written: int, __: int, speed: float) -> None:
            package.update_progress(total_bytes_written, speed)

            total_downloaded = 0
            total_size = 0
            current_speed = 0.0
            for dependency in task.dependencies_to_download:
                for candidate in dependency.packages:
                    total_size += candidate.download_size
                    if candidate.downloaded:
                        total_downloaded += candidate.download_size
                    elif candidate.id == package.id:
                        total_downloaded += total_bytes_written
                        current_speed = speed

            task.total_size = total_size
            task.total_downloaded_size = total_downloaded
            task.total_progress = float(total_downloaded) / float(total_size) if total_size > 0 else 0.0
            task.total_speed = current_speed

        def should_cancel() -> bool:
            return self.cancel_tracker.is_cancelled(task.id) or self.cancel_tracker.is_paused(task.id)

        download_task = asyncio.create_task(
            self._stream_download(
                url=target_url,
                destination_url=destination_url,
                headers=NetworkConstants.download_headers(),
                progress_handler=on_progress,
                cancellation_handler=should_cancel,
            )
        )
        self.cancel_tracker.register_task(task.id, download_task)

        try:
            await download_task
        finally:
            self.cancel_tracker.cleanup_completed_tasks()

        package.mark_as_completed()

        total_downloaded = 0
        total_size = 0
        for dependency in task.dependencies_to_download:
            for candidate in dependency.packages:
                total_size += candidate.download_size
                if candidate.downloaded:
                    total_downloaded += candidate.download_size

        task.total_size = total_size
        task.total_downloaded_size = total_downloaded
        task.total_progress = float(total_downloaded) / float(total_size) if total_size > 0 else 0.0
        task.total_speed = 0.0

        if all(package.downloaded for dependency in task.dependencies_to_download for package in dependency.packages):
            task.set_status(
                TaskStatus(
                    "completed",
                    CompletionInfo(
                        timestamp=_utcnow(),
                        total_time=(_utcnow() - task.created_at).total_seconds(),
                        total_size=total_size,
                    ),
                )
            )

        product.update_completed_packages()
        await self._persist_task(task)

    async def handle_error(self, task_id: uuid.UUID, error: BaseException) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return

        error_message, is_recoverable = self.classify_error(error)

        if is_recoverable and task.retry_count < NetworkConstants.MAX_RETRY_ATTEMPTS:
            task.retry_count += 1
            next_retry_date = _utcnow() + timedelta(seconds=NetworkConstants.RETRY_DELAY)
            task.set_status(
                TaskStatus(
                    "retrying",
                    RetryInfo(
                        attempt=task.retry_count,
                        max_attempts=NetworkConstants.MAX_RETRY_ATTEMPTS,
                        reason=error_message,
                        next_retry_date=next_retry_date,
                    ),
                )
            )

            async def retry_later() -> None:
                try:
                    await asyncio.sleep(NetworkConstants.RETRY_DELAY)
                    if not self.cancel_tracker.is_cancelled(task_id):
                        await self.resume_download_task(task_id)
                except asyncio.CancelledError:
                    return

            asyncio.create_task(retry_later())
            return

        task.set_status(
            TaskStatus(
                "failed",
                FailureInfo(
                    message=error_message,
                    error=error,
                    timestamp=_utcnow(),
                    recoverable=is_recoverable,
                ),
            )
        )

        if not is_recoverable and task.current_package is not None:
            dependency = self._find_dependency_for_package(task, task.current_package)
            if dependency is not None:
                package_file = task.directory / dependency.sap_code / task.current_package.full_package_name
                package_file.unlink(missing_ok=True)
                package_identifier = self.generate_package_identifier(task.current_package, task, dependency)
                self.chunked_downloader.clear_chunked_download_state(package_identifier)

        await self._persist_task(task)

    async def resume_download_task(self, task_id: uuid.UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return

        self.cancel_tracker.resume(task_id)

        total_packages = sum(len(dependency.packages) for dependency in task.dependencies_to_download)
        task.set_status(
            TaskStatus(
                "downloading",
                DownloadInfo(
                    file_name=task.current_package.full_package_name if task.current_package else "",
                    current_package_index=0,
                    total_packages=total_packages,
                    start_time=_utcnow(),
                    estimated_time_remaining=None,
                ),
            )
        )

        await self._persist_task(task)

        if task.product_id == "APRO":
            if task.current_package is not None and task.dependencies_to_download:
                try:
                    await self.download_package_direct(
                        package=task.current_package,
                        task=task,
                        product=task.dependencies_to_download[0],
                        url=task.current_package.download_url,
                        destination_directory=task.directory.parent,
                    )
                except BaseException as error:
                    if not self._is_pause_related(error):
                        await self.handle_error(task_id, error)
        else:
            await self.start_concurrent_download_process(task)

    def classify_error(self, error: BaseException) -> tuple[str, bool]:
        translated = _translate_exception(error)
        if translated.kind == "server_unreachable":
            return "Server is unreachable", True
        if translated.kind == "timeout":
            return "Download timed out", True
        if translated.kind == "file_permission_denied":
            return "Write permission denied", False
        if translated.kind == "cancelled":
            return "Download cancelled", False
        return translated.message, translated.recoverable

    async def get_application_info(self, build_guid: str) -> str:
        if not build_guid:
            raise DownloadFailure("buildGuid is empty", kind="invalid_data", recoverable=False)
        headers = NetworkConstants.adobe_request_headers(self.api_version)
        headers["x-adobe-build-guid"] = build_guid
        response = await _http_request(
            NetworkConstants.APPLICATION_JSON_URL,
            headers=headers,
            proxy_url=self.proxy_url,
        )
        return response.data.decode("utf-8", errors="ignore")

    async def fetch_products_catalog(self) -> ResolvedCatalog:
        response = await _http_request(
            NetworkConstants.products_catalog_url(self.api_version, self._catalog_platforms()),
            headers=NetworkConstants.adobe_request_headers(self.api_version),
            proxy_url=self.proxy_url,
        )
        catalog = self.parse_products_catalog(response.data.decode("utf-8", errors="ignore"))
        self.cdn = catalog.secure_cdn
        self.products = catalog.ccm_products
        return catalog

    def parse_products_catalog(self, json_text: str) -> ResolvedCatalog:
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise DownloadFailure("Invalid products/all JSON", kind="invalid_data", recoverable=False, cause=error) from error

        channels = self._extract_catalog_channels(payload)
        if not channels:
            raise DownloadFailure("No channel data found in products/all response", kind="invalid_data", recoverable=False)

        secure_cdn = self._extract_secure_cdn(channels)
        if not secure_cdn:
            raise DownloadFailure("Unable to resolve secure CDN from products/all response", kind="invalid_data", recoverable=False)

        sti_products = self._parse_catalog_products(channels, "sti", [])
        ccm_products = self._parse_catalog_products(channels, "ccm", sti_products)
        return ResolvedCatalog(secure_cdn=secure_cdn, ccm_products=ccm_products, sti_products=sti_products)

    async def resolve_download_plan(self, product_id: str, version: str) -> ResolvedDownloadPlan:
        catalog = await self.fetch_products_catalog()
        self.cdn = catalog.secure_cdn
        self.products = catalog.ccm_products
        if product_id == "APRO":
            return await self.resolve_apro_download_plan(product_id, version, catalog=catalog)
        return await self.resolve_standard_download_plan(product_id, version, catalog=catalog)

    async def resolve_standard_download_plan(
        self,
        product_id: str,
        version: str,
        *,
        catalog: ResolvedCatalog | None = None,
    ) -> ResolvedDownloadPlan:
        catalog = catalog or await self.fetch_products_catalog()
        product = catalog.find_ccm_product(product_id, version)
        if product is None:
            raise DownloadFailure(
                f"Product {product_id} {version} was not found in the CCM catalog",
                kind="invalid_data",
                recoverable=False,
            )
        selected_platform = self._select_platform(product.platforms)
        if selected_platform is None:
            raise DownloadFailure(
                f"Product {product_id} {version} has no compatible platforms for {self.target_platform}",
                kind="invalid_data",
                recoverable=False,
            )

        first_language_set = selected_platform.language_sets[0]
        dependency_requests = [
            DependenciesToDownload(
                sap_code=product.id,
                version=product.version,
                build_guid=first_language_set.build_guid,
            )
        ]
        for dependency in first_language_set.dependencies:
            dependency_requests.append(
                DependenciesToDownload(
                    sap_code=dependency.sap_code,
                    version=dependency.product_version,
                    build_guid=dependency.build_guid,
                )
            )

        dependencies: list[DependenciesToDownload] = []
        for dependency_request in dependency_requests:
            application_json = await self.get_application_info(dependency_request.build_guid)
            packages = self._extract_resolved_packages(
                product_id=product.id,
                dependency_sap_code=dependency_request.sap_code,
                secure_cdn=catalog.secure_cdn,
                application_json=application_json,
            )
            dependencies.append(
                DependenciesToDownload(
                    sap_code=dependency_request.sap_code,
                    version=dependency_request.version,
                    build_guid=dependency_request.build_guid,
                    application_json=application_json,
                    packages=packages,
                )
            )

        return ResolvedDownloadPlan(
            product_id=product.id,
            product_version=product.version,
            display_name=product.display_name,
            secure_cdn=catalog.secure_cdn,
            dependencies=dependencies,
            is_apro=False,
        )

    async def resolve_apro_download_plan(
        self,
        product_id: str,
        version: str,
        *,
        catalog: ResolvedCatalog | None = None,
    ) -> ResolvedDownloadPlan:
        catalog = catalog or await self.fetch_products_catalog()
        product = catalog.find_ccm_product(product_id, version)
        if product is None:
            raise DownloadFailure(f"APRO {version} was not found in the CCM catalog", kind="invalid_data", recoverable=False)
        selected_platform = self._select_platform(product.platforms)
        if selected_platform is None:
            raise DownloadFailure(
                f"APRO {version} has no compatible platforms for {self.target_platform}",
                kind="invalid_data",
                recoverable=False,
            )

        first_language_set = selected_platform.language_sets[0]
        manifest_url = self._normalize_catalog_url(catalog.secure_cdn, first_language_set.manifest_url)
        manifest_response = await _http_request(
            manifest_url,
            headers=NetworkConstants.adobe_request_headers(self.api_version),
            proxy_url=self.proxy_url,
        )
        try:
            manifest_root = ET.fromstring(manifest_response.data)
        except ET.ParseError as error:
            raise DownloadFailure("Invalid APRO manifest XML", kind="invalid_data", recoverable=False, cause=error) from error

        asset_path = manifest_root.findtext(".//asset_list/asset/asset_path")
        asset_size_text = manifest_root.findtext(".//asset_list/asset/asset_size")
        if not asset_path or not asset_size_text:
            raise DownloadFailure("APRO manifest is missing asset_path or asset_size", kind="invalid_data", recoverable=False)

        package_url = self._normalize_catalog_url(catalog.secure_cdn, asset_path)
        fallback_name = f"Adobe Downloader {product_id}_{first_language_set.product_version or 'unknown'}_{selected_platform.id}"
        package_name = self._package_name_from_download_url(package_url, fallback_name)
        package_type = self._package_type_from_download_url(package_url, default_type="package")

        dependency = DependenciesToDownload(
            sap_code=product.id,
            version=first_language_set.product_version,
            build_guid=first_language_set.build_guid,
            packages=[
                Package(
                    type=package_type,
                    full_package_name=package_name,
                    download_size=int(asset_size_text),
                    download_url=package_url,
                    package_version=first_language_set.product_version,
                    is_required=True,
                    is_selected=True,
                )
            ],
        )
        return ResolvedDownloadPlan(
            product_id=product.id,
            product_version=product.version,
            display_name=product.display_name,
            secure_cdn=catalog.secure_cdn,
            dependencies=[dependency],
            is_apro=True,
        )

    def apply_download_plan(self, task: DownloadTask, plan: ResolvedDownloadPlan) -> None:
        self.cdn = plan.secure_cdn
        task.display_name = plan.display_name
        task.dependencies_to_download = plan.dependencies
        task.total_size = sum(package.download_size for dependency in plan.dependencies for package in dependency.packages)
        task.total_packages = sum(len(dependency.packages) for dependency in plan.dependencies)
        task.completed_packages = sum(1 for dependency in plan.dependencies for package in dependency.packages if package.downloaded)
        task.display_install_button = not plan.is_apro

    def _extract_catalog_channels(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if int(self.api_version) == 6:
            channels = payload.get("channels", {})
            channel_array = channels.get("channel", []) if isinstance(channels, dict) else channels
        else:
            channel_array = payload.get("channel", [])
        return [channel for channel in _ensure_list(channel_array) if isinstance(channel, dict)]

    def _extract_secure_cdn(self, channels: list[dict[str, Any]]) -> str:
        for channel in channels:
            cdn = channel.get("cdn")
            if not isinstance(cdn, dict):
                continue
            secure_cdn = cdn.get("secure")
            if isinstance(secure_cdn, str) and secure_cdn:
                return secure_cdn
        return ""

    def _parse_catalog_products(
        self,
        channels: list[dict[str, Any]],
        channel_name: str,
        sti_products: list[Product],
    ) -> list[Product]:
        products: list[Product] = []
        for channel in channels:
            if channel.get("name") != channel_name:
                continue

            products_block = channel.get("products") or {}
            product_array = products_block.get("product", []) if isinstance(products_block, dict) else products_block
            for product_payload in _ensure_list(product_array):
                if not isinstance(product_payload, dict):
                    continue
                product_id = product_payload.get("id")
                display_name = product_payload.get("displayName")
                version = product_payload.get("version")
                if not isinstance(product_id, str) or not isinstance(display_name, str) or not isinstance(version, str):
                    continue
                if channel_name == "ccm" and display_name in {"Creative Cloud", "Substance Alchemist"}:
                    continue
                products.append(
                    Product(
                        id=product_id,
                        version=version,
                        display_name=display_name,
                        platforms=self._parse_catalog_platforms(product_payload, sti_products),
                    )
                )
        return products

    def _parse_catalog_platforms(self, product_payload: dict[str, Any], sti_products: list[Product]) -> list[Platform]:
        parsed_platforms: list[Platform] = []
        platforms_block = product_payload.get("platforms") or {}
        platform_array = platforms_block.get("platform", []) if isinstance(platforms_block, dict) else platforms_block

        for platform_payload in _ensure_list(platform_array):
            if not isinstance(platform_payload, dict):
                continue
            platform_id = platform_payload.get("id")
            language_set_payload = _ensure_list(platform_payload.get("languageSet"))
            if not isinstance(platform_id, str) or not language_set_payload:
                continue

            first_language_set = language_set_payload[0]
            if not isinstance(first_language_set, dict):
                continue
            urls = first_language_set.get("urls") or {}
            dependencies = self._parse_dependency_metadata(first_language_set, sti_products)
            parsed_platforms.append(
                Platform(
                    id=platform_id,
                    language_sets=[
                        LanguageSet(
                            manifest_url=urls.get("manifestURL", "") if isinstance(urls, dict) else "",
                            dependencies=dependencies,
                            build_guid=str(first_language_set.get("buildGuid", "") or ""),
                            base_version=str(first_language_set.get("baseVersion", "") or ""),
                            product_version=str(first_language_set.get("productVersion", "") or ""),
                            product_code=str(first_language_set.get("productCode", "") or ""),
                            name=str(first_language_set.get("name", "") or ""),
                            install_size=int(first_language_set.get("installSize", 0) or 0),
                        )
                    ],
                )
            )
        return parsed_platforms

    def _parse_dependency_metadata(self, language_set_payload: dict[str, Any], sti_products: list[Product]) -> list[DependencyInfo]:
        dependencies_block = language_set_payload.get("dependencies") or {}
        dependency_array = dependencies_block.get("dependency", []) if isinstance(dependencies_block, dict) else dependencies_block
        dependencies: list[DependencyInfo] = []
        for dependency_payload in _ensure_list(dependency_array):
            if not isinstance(dependency_payload, dict):
                continue
            sap_code = dependency_payload.get("sapCode")
            base_version = dependency_payload.get("baseVersion")
            if not isinstance(sap_code, str) or not isinstance(base_version, str):
                continue
            dependencies.append(self._resolve_dependency_metadata(sap_code, base_version, sti_products))
        return dependencies

    def _resolve_dependency_metadata(self, sap_code: str, base_version: str, sti_products: list[Product]) -> DependencyInfo:
        catalog_products = [product for product in sti_products if product.id == sap_code]
        catalog_products.sort(key=cmp_to_key(lambda left, right: compare_version_strings(left.version, right.version)), reverse=True)

        if not catalog_products:
            return DependencyInfo(
                sap_code=sap_code,
                base_version=base_version,
                product_version="",
                build_guid="",
                selected_platform="",
            )

        latest_product = catalog_products[0]
        selected_platform = self._select_platform(latest_product.platforms)

        if selected_platform is None or not selected_platform.language_sets:
            return DependencyInfo(
                sap_code=sap_code,
                base_version=base_version,
                product_version="",
                build_guid="",
                selected_platform="",
            )

        selected_language_set = selected_platform.language_sets[0]
        return DependencyInfo(
            sap_code=sap_code,
            base_version=base_version,
            product_version=selected_language_set.product_version,
            build_guid=selected_language_set.build_guid,
            selected_platform=selected_platform.id,
        )

    def _extract_resolved_packages(
        self,
        *,
        product_id: str,
        dependency_sap_code: str,
        secure_cdn: str,
        application_json: str,
    ) -> list[Package]:
        try:
            payload = json.loads(application_json)
        except json.JSONDecodeError as error:
            raise DownloadFailure("Invalid application.json payload", kind="invalid_data", recoverable=False, cause=error) from error

        packages_block = payload.get("Packages")
        if not isinstance(packages_block, dict):
            raise DownloadFailure("application.json does not contain a Packages block", kind="invalid_data", recoverable=False)

        resolved_packages: list[Package] = []
        for package_payload in _ensure_list(packages_block.get("Package", [])):
            if not isinstance(package_payload, dict):
                continue
            raw_path = package_payload.get("Path")
            if not isinstance(raw_path, str) or not raw_path:
                continue

            package_version = str(package_payload.get("PackageVersion", "") or "")
            full_package_name = package_payload.get("fullPackageName")
            if not isinstance(full_package_name, str) or not full_package_name:
                package_name = package_payload.get("PackageName")
                if not isinstance(package_name, str) or not package_name:
                    continue
                full_package_name = f"{package_name}.zip"

            download_size_raw = package_payload.get("DownloadSize", 0)
            if isinstance(download_size_raw, str):
                try:
                    download_size = int(download_size_raw)
                except ValueError:
                    download_size = 0
            elif isinstance(download_size_raw, (int, float)):
                download_size = int(download_size_raw)
            else:
                download_size = 0

            package_type = str(package_payload.get("Type", "non-core") or "non-core")
            condition = str(package_payload.get("Condition", "") or "")
            is_selected, is_required = self._default_selection_state(
                product_id=product_id,
                dependency_sap_code=dependency_sap_code,
                package_type=package_type,
                full_package_name=full_package_name,
                condition=condition,
            )

            validation_url = None
            for validation_key in ("validationURL", "ValidationURL", "validationUrl"):
                validation_value = package_payload.get(validation_key)
                if isinstance(validation_value, str) and validation_value:
                    validation_url = self._normalize_catalog_url(secure_cdn, validation_value)
                    break

            resolved_packages.append(
                Package(
                    type=package_type,
                    full_package_name=full_package_name,
                    download_size=download_size,
                    download_url=self._normalize_catalog_url(secure_cdn, raw_path),
                    package_version=package_version,
                    validation_url=validation_url,
                    condition=condition,
                    is_required=is_required,
                    is_selected=is_selected,
                )
            )
        return resolved_packages

    def _default_selection_state(
        self,
        *,
        product_id: str,
        dependency_sap_code: str,
        package_type: str,
        full_package_name: str,
        condition: str,
    ) -> tuple[bool, bool]:
        is_core = package_type == "core"
        install_language = f"[installLanguage]=={self.default_language}"

        selected_by_default = False
        required = False
        if dependency_sap_code == product_id:
            if is_core:
                selected_by_default = (
                    not condition
                    or self._matches_target_architecture(condition)
                    or install_language in condition
                    or self.default_language == "ALL"
                )
                required = selected_by_default
            else:
                selected_by_default = install_language in condition or self.default_language == "ALL"
        else:
            selected_by_default = (
                not condition
                or ("[OSVersion]" in condition and self.check_os_version_condition(condition))
                or install_language in condition
                or self.default_language == "ALL"
            )

        if "SuperCafModels" in full_package_name:
            selected_by_default = True
        return selected_by_default, required

    def check_os_version_condition(self, condition: str) -> bool:
        matches = re.findall(r"\[OSVersion\](>=|<=|<|>|==)([\d.]+)", condition)
        if not matches:
            return False
        for operator, required_version_text in matches:
            try:
                required_version = float(required_version_text)
            except ValueError:
                return False
            if not self.compare_versions(self.current_os_version, required_version, operator):
                return False
        return True

    @staticmethod
    def _normalize_catalog_url(secure_cdn: str, path: str) -> str:
        parsed = urllib.parse.urlparse(path)
        if parsed.scheme and parsed.netloc:
            return path
        clean_cdn = secure_cdn.rstrip("/")
        clean_path = path if path.startswith("/") else f"/{path}"
        return clean_cdn + clean_path

    def compare_versions(self, current: float, required: float, operator: str) -> bool:
        if operator == ">=":
            return current >= required
        if operator == "<=":
            return current <= required
        if operator == ">":
            return current > required
        if operator == "<":
            return current < required
        if operator == "==":
            return current == required
        return False

    async def execute_privileged_command(self, command: str) -> str:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode("utf-8", errors="ignore").strip()
        error_output = stderr.decode("utf-8", errors="ignore").strip()
        if process.returncode != 0:
            return f"Error: {error_output or output or f'command failed with exit code {process.returncode}'}"
        return output

    def generate_driver_xml(
        self,
        *,
        version: str,
        language: str,
        product_info: Product,
        display_name: str,
        platform_id: str | None = None,
        modules: list[dict[str, Any]] | None = None,
    ) -> str:
        matching_product = self._find_product(product_info.id, version)
        if matching_product is None or not matching_product.platforms:
            return ""

        platform = self._select_platform(matching_product.platforms, preferred_platform=platform_id)
        if platform is None:
            return ""
        language_set = platform.language_sets[0]

        root = ET.Element("DriverInfo")
        product_element = ET.SubElement(root, "ProductInfo")
        dependencies_element = ET.SubElement(product_element, "Dependencies")
        for dependency in language_set.dependencies:
            dependency_element = ET.SubElement(dependencies_element, "Dependency")
            ET.SubElement(dependency_element, "BuildGuid").text = dependency.build_guid
            ET.SubElement(dependency_element, "BuildVersion").text = dependency.product_version
            ET.SubElement(dependency_element, "CodexVersion").text = dependency.base_version
            ET.SubElement(dependency_element, "Platform").text = dependency.selected_platform or platform.id
            ET.SubElement(dependency_element, "SAPCode").text = dependency.sap_code
            ET.SubElement(dependency_element, "EsdDirectory").text = dependency.sap_code

        modules_element = ET.SubElement(product_element, "Modules")
        for module in modules or []:
            module_id = module.get("Id") or module.get("id")
            if not isinstance(module_id, str):
                continue
            module_element = ET.SubElement(modules_element, "Module")
            ET.SubElement(module_element, "Id").text = module_id
            ET.SubElement(module_element, "Baseline").text = "false"

        ET.SubElement(product_element, "BuildGuid").text = language_set.build_guid
        ET.SubElement(product_element, "BuildVersion").text = language_set.product_version
        ET.SubElement(product_element, "CodexVersion").text = product_info.version
        ET.SubElement(product_element, "Platform").text = platform.id
        ET.SubElement(product_element, "EsdDirectory").text = product_info.id
        ET.SubElement(product_element, "SAPCode").text = product_info.id

        request_info = ET.SubElement(root, "RequestInfo")
        ET.SubElement(request_info, "InstallDir").text = self._default_install_directory()
        ET.SubElement(request_info, "InstallLanguage").text = language

        rough_xml = ET.tostring(root, encoding="utf-8")
        pretty_xml = xml.dom.minidom.parseString(rough_xml).toprettyxml(indent="    ")
        return "\n".join(line for line in pretty_xml.splitlines() if line.strip() and not line.startswith("<?xml"))

    async def download_apro(self, task: DownloadTask, product_info: Product) -> None:
        self.register_task(task)
        selected_platform = self._select_platform(product_info.platforms, preferred_platform=task.platform)
        if selected_platform is None:
            raise DownloadFailure(
                f"APRO manifest metadata is missing for {task.platform or self.target_platform}",
                kind="invalid_data",
                recoverable=False,
            )

        first_language_set = selected_platform.language_sets[0]
        manifest_url = self._normalize_download_url(first_language_set.manifest_url)
        headers = NetworkConstants.adobe_request_headers(self.api_version)
        manifest_response = await _http_request(manifest_url, headers=headers, proxy_url=self.proxy_url)
        manifest_root = ET.fromstring(manifest_response.data)

        download_path = manifest_root.findtext(".//asset_list/asset/asset_path")
        asset_size_text = manifest_root.findtext(".//asset_list/asset/asset_size")
        if not download_path or not asset_size_text:
            raise DownloadFailure("Unable to find APRO asset metadata", kind="invalid_data", recoverable=False)

        apro_download_url = self._normalize_download_url(download_path)
        asset_size = int(asset_size_text)
        fallback_name = (
            f"Adobe Downloader {task.product_id}_{first_language_set.product_version or 'unknown'}_{selected_platform.id or 'unknown'}"
        )
        apro_package = Package(
            type=self._package_type_from_download_url(apro_download_url, default_type="package"),
            full_package_name=self._package_name_from_download_url(apro_download_url, fallback_name),
            download_size=asset_size,
            download_url=apro_download_url,
            package_version="",
        )

        product = DependenciesToDownload(
            sap_code=task.product_id,
            version=first_language_set.product_version or "unknown",
            build_guid="",
            packages=[apro_package],
        )
        task.dependencies_to_download = [product]
        task.total_size = asset_size
        task.current_package = apro_package
        task.set_status(
            TaskStatus(
                "downloading",
                DownloadInfo(
                    file_name=apro_package.full_package_name,
                    current_package_index=0,
                    total_packages=1,
                    start_time=_utcnow(),
                    estimated_time_remaining=None,
                ),
            )
        )

        await self.download_package_direct(
            package=apro_package,
            task=task,
            product=product,
            url=apro_download_url,
            destination_directory=task.directory.parent,
        )

    async def pause_download_task(self, task_id: uuid.UUID, reason: str | PauseReason) -> None:
        self.cancel_tracker.pause(task_id)
        task = self.tasks.get(task_id)
        if task is None:
            return

        for dependency in task.dependencies_to_download:
            for package in dependency.packages:
                package_identifier = self.generate_package_identifier(package, task, dependency)
                await self.chunked_downloader.pause_download(package_identifier)

        await asyncio.sleep(0.2)
        self.cancel_tracker.cleanup_completed_tasks()

        task.set_status(
            TaskStatus(
                "paused",
                PauseInfo(reason=reason, timestamp=_utcnow(), resumable=True),
            )
        )
        for dependency in task.dependencies_to_download:
            for package in dependency.packages:
                if package.status == PackageStatus.DOWNLOADING:
                    package.status = PackageStatus.PAUSED

        await self._persist_task(task)

    async def download_creative_cloud_helper_packages(
        self,
        *,
        progress_handler: Callback,
        cancellation_handler: Callback,
        should_process: bool = True,
        target_directory: Path | None = None,
    ) -> None:
        platform_name = self._creative_cloud_platform_name()
        base_url = (
            "https://cdn-ffc.oobesaas.adobe.com/core/v1/applications"
            f"?name=CreativeCloud&platform={platform_name}"
        )

        temp_directory = Path(tempfile.mkdtemp(prefix="adobe_cc_"))
        try:
            response = await _http_request(
                base_url,
                headers=NetworkConstants.download_headers(),
                proxy_url=self.proxy_url,
            )
            xml_root = ET.fromstring(response.data)

            secure_cdn = (xml_root.findtext(".//cdn/secure") or "").strip()
            packages_to_download: list[tuple[str, str, int]] = []
            package_sets = {
                (package_set.findtext("name") or "").strip(): package_set
                for package_set in xml_root.findall(".//packageSet")
            }

            for package_set_name, package_name in self._creative_cloud_helper_targets():
                package_set = package_sets.get(package_set_name)
                if package_set is None:
                    continue
                package_node = None
                for candidate in package_set.findall(".//package"):
                    if (candidate.findtext("name") or "").strip() == package_name:
                        package_node = candidate
                        break
                if package_node is None:
                    continue

                manifest_path = (package_node.findtext(".//manifestUrl") or "").strip()
                if not manifest_path:
                    continue
                manifest_url = urllib.parse.urljoin(secure_cdn, manifest_path)
                manifest_response = await _http_request(
                    manifest_url,
                    headers=NetworkConstants.download_headers(),
                    proxy_url=self.proxy_url,
                )
                manifest_root = ET.fromstring(manifest_response.data)

                asset_path = (manifest_root.findtext(".//asset_path") or "").strip()
                size_text = (manifest_root.findtext(".//asset_size") or "0").strip()
                if not asset_path or not size_text:
                    continue

                packages_to_download.append((package_name, asset_path, int(size_text)))

            if not packages_to_download:
                raise DownloadFailure("No Creative Cloud helper packages were found", kind="invalid_data", recoverable=False)

            total_count = len(packages_to_download)
            for index, (package_name, package_url, _) in enumerate(packages_to_download):
                if bool(cancellation_handler()):
                    raise DownloadCancelled()

                await _invoke_callback(progress_handler, float(index) / float(total_count), f"Downloading {package_name}...")
                destination_url = temp_directory / f"{package_name}.zip"
                await self._stream_download(
                    url=package_url,
                    destination_url=destination_url,
                    headers=NetworkConstants.download_headers(),
                    cancellation_handler=cancellation_handler,
                )

            await _invoke_callback(
                progress_handler,
                0.9,
                "Installing components..." if should_process else "Finalizing download...",
            )

            target_directory = target_directory or self.cc_target_directory
            target_directory.mkdir(parents=True, exist_ok=True)

            for package_name, _, _ in packages_to_download:
                package_dir = target_directory / package_name
                shutil.rmtree(package_dir, ignore_errors=True)
                package_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(temp_directory / f"{package_name}.zip") as archive:
                    archive.extractall(package_dir)

            if should_process:
                await _invoke_callback(self.setup_modifier)

            await _invoke_callback(
                progress_handler,
                1.0,
                "Install complete" if should_process else "Download complete",
            )
        finally:
            shutil.rmtree(temp_directory, ignore_errors=True)

    async def cancel_download_task(self, task_id: uuid.UUID, remove_files: bool = False) -> None:
        self.cancel_tracker.cancel(task_id)
        task = self.tasks.get(task_id)
        if task is None:
            return

        for dependency in task.dependencies_to_download:
            for package in dependency.packages:
                package_identifier = self.generate_package_identifier(package, task, dependency)
                await self.chunked_downloader.cancel_download(package_identifier)

        if remove_files:
            shutil.rmtree(task.directory, ignore_errors=True)

        task.set_status(
            TaskStatus(
                "failed",
                FailureInfo(
                    message="Download cancelled",
                    error=DownloadCancelled(),
                    timestamp=_utcnow(),
                    recoverable=False,
                ),
            )
        )
        await self._persist_task(task)


