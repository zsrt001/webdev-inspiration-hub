from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class WheelFile:
    filename: str
    url: str
    sha256: str


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    # Force no proxy even if HTTP(S)_PROXY is set in env.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_tag() -> str:
    if sys.platform.startswith("win"):
        return "win_amd64"
    raise RuntimeError(f"Unsupported platform for this helper: {sys.platform}")


def _fetch_json(url: str, timeout: float) -> dict:
    opener = _no_proxy_opener()
    with opener.open(url, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def _pick_best_wheel(files: list[WheelFile], py_tag: str, plat_tag: str) -> WheelFile:
    # Prefer non-free-threaded builds on default CPython.
    preferred = [f for f in files if f"{py_tag}-{py_tag}-{plat_tag}.whl" in f.filename]
    if preferred:
        return preferred[0]

    # Fallback to free-threaded build if that's the only option available.
    preferred_t = [f for f in files if f"{py_tag}-{py_tag}t-{plat_tag}.whl" in f.filename]
    if preferred_t:
        return preferred_t[0]

    raise RuntimeError(f"No matching wheel for {py_tag} / {plat_tag}")


def _download(url: str, dest_path: str, timeout: float) -> None:
    opener = _no_proxy_opener()
    with opener.open(url, timeout=timeout) as resp:
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


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Pillow wheel for offline install.")
    parser.add_argument("--version", default="11.0.0")
    parser.add_argument("--dest", default=os.path.join("backend", "wheels"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    py_tag = _python_tag()
    plat_tag = _platform_tag()
    version = str(args.version).strip()

    api_url = f"https://pypi.org/pypi/Pillow/{version}/json"
    print(f"[pillow] Fetching metadata: {api_url}")
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
        print(f"[pillow] Wheel already exists: {dest_path}")
    else:
        print(f"[pillow] Downloading: {chosen.filename}")
        _download(chosen.url, dest_path, timeout=args.timeout)

    got = _sha256_file(dest_path)
    if got.lower() != chosen.sha256.lower():
        raise RuntimeError(f"SHA256 mismatch for {dest_path}: got={got} expected={chosen.sha256}")

    print(f"[pillow] OK: {dest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

