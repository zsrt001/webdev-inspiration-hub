from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass

USER_AGENT = "ai-wedding-studio-wheelhouse/1.0"


@dataclass(frozen=True)
class WheelFile:
    filename: str
    url: str
    sha256: str


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    # Force no proxy even if HTTP(S)_PROXY is set in env (common in locked-down shells).
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_tag() -> str:
    if sys.platform.startswith("win"):
        return "win_amd64"
    raise RuntimeError(f"Unsupported platform for this helper: {sys.platform}")


def _fetch_json(url: str, timeout: float, *, retries: int = 6) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            opener = _no_proxy_opener()
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with opener.open(req, timeout=timeout) as resp:
                data = resp.read()
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.6 * (2**attempt))
                continue
            raise
    raise RuntimeError(f"Failed to fetch JSON: {url}") from last_err


def _pick_best_wheel(files: list[WheelFile], py_tag: str, plat_tag: str) -> WheelFile:
    def _cp_tag_for_minor(minor: int) -> str:
        # 3.10 -> cp310, 3.9 -> cp39
        return f"cp3{minor}"

    # Prefer platform-specific CPython wheels first.
    preferred_patterns: list[str] = [
        f"{py_tag}-{py_tag}-{plat_tag}.whl",
        f"{py_tag}-{py_tag}t-{plat_tag}.whl",  # free-threaded (rare)
        f"{py_tag}-abi3-{plat_tag}.whl",
    ]

    # Accept older abi3 wheels (e.g. cp39-abi3) which are compatible with newer CPython.
    current_minor = sys.version_info.minor
    for minor in range(current_minor, 6, -1):
        preferred_patterns.append(f"{_cp_tag_for_minor(minor)}-abi3-{plat_tag}.whl")

    for pat in preferred_patterns:
        for f in files:
            if pat in f.filename:
                return f

    # Pure-Python wheels.
    for suffix in ("-py3-none-any.whl", "-py2.py3-none-any.whl"):
        for f in files:
            if f.filename.endswith(suffix):
                return f

    # Last resort: any wheel.
    if files:
        return files[0]

    raise RuntimeError(f"No wheels found for {py_tag} / {plat_tag}")


def _download(url: str, dest_path: str, timeout: float, *, retries: int = 6) -> None:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            opener = _no_proxy_opener()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout) as resp:
                total = int(resp.headers.get("content-length") or 0)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with open(dest_path, "wb") as f:
                    downloaded = 0
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = int(downloaded * 100 / total)
                            print(f"[download] {pct}% ({downloaded}/{total})", end="\r")
            if total:
                print("")
            return
        except Exception as e:
            last_err = e
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except OSError:
                pass
            if attempt < retries - 1:
                time.sleep(0.8 * (2**attempt))
                continue
            raise
    raise RuntimeError(f"Failed to download wheel: {url}") from last_err


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a wheel from PyPI for offline install.")
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dest", default=os.path.join("backend", "wheels"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    package = str(args.package).strip()
    version = str(args.version).strip()
    if not package or not version:
        raise ValueError("package/version required")

    py_tag = _python_tag()
    plat_tag = _platform_tag()

    api_url = f"https://pypi.org/pypi/{package}/{version}/json"
    print(f"[wheel] Fetching metadata: {api_url}")
    payload = _fetch_json(api_url, timeout=args.timeout)

    urls = payload.get("urls") or []
    wheel_files: list[WheelFile] = []
    for item in urls:
        filename = (item.get("filename") or "").strip()
        if not filename.endswith(".whl"):
            continue
        url = (item.get("url") or "").strip()
        sha256 = ((item.get("digests") or {}).get("sha256") or "").strip()
        if not url or not sha256:
            continue
        wheel_files.append(WheelFile(filename=filename, url=url, sha256=sha256))

    chosen = _pick_best_wheel(wheel_files, py_tag=py_tag, plat_tag=plat_tag)
    dest_dir = os.path.abspath(args.dest)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, chosen.filename)

    if os.path.exists(dest_path):
        print(f"[wheel] Wheel already exists: {dest_path}")
    else:
        print(f"[wheel] Downloading: {chosen.filename}")
        _download(chosen.url, dest_path, timeout=args.timeout)

    got = _sha256_file(dest_path)
    if got.lower() != chosen.sha256.lower():
        raise RuntimeError(f"SHA256 mismatch for {dest_path}: got={got} expected={chosen.sha256}")

    print(f"[wheel] OK: {dest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
