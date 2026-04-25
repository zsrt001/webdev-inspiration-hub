from __future__ import annotations

"""
Build an offline-friendly wheelhouse and vendor-install it into
`backend/app/_vendor_pkgs/<fingerprint>` and write `backend/app/_vendor_path.txt`
as a pointer to the active vendor directory.

Why this exists:
- `pip install` can be blocked by proxy/no-index policies in some environments.
- We want a reproducible set of wheels + a one-click "vendoring" install that makes
  `backend/app` importable without touching global site-packages.

This script uses PyPI's JSON API via urllib with proxies disabled (see `download_wheel.py`).
It resolves dependencies from `requires_dist` with basic marker evaluation.

Usage:
  python backend/scripts/vendor_wheelhouse.py bootstrap
  python backend/scripts/vendor_wheelhouse.py sync
  python backend/scripts/vendor_wheelhouse.py install
"""

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import download_wheel


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_REQUIREMENTS = BACKEND_DIR / "requirements.txt"
DEFAULT_WHEELS_DIR = BACKEND_DIR / "wheels"
DEFAULT_VENDOR_DIR = BACKEND_DIR / "app" / "_vendor"
DEFAULT_LOCK_PATH = DEFAULT_WHEELS_DIR / "vendor_lock.json"

_JSON_CACHE: dict[str, dict[str, Any]] = {}


def _fetch_json(url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    cached = _JSON_CACHE.get(url)
    if cached is not None:
        return cached
    payload = download_wheel._fetch_json(url, timeout=timeout)  # noqa: SLF001
    if not isinstance(payload, dict):
        raise TypeError(f"Expected JSON object for {url}")
    _JSON_CACHE[url] = payload
    return payload


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _version_key(version: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", version)
    return tuple(int(n) for n in nums[:6])


def _is_strict_release(version: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)*", version.strip()))


@dataclass
class Requirement:
    name: str
    spec: str = ""
    extras: set[str] = field(default_factory=set)


def _split_marker(req: str) -> tuple[str, str | None]:
    if ";" not in req:
        return req.strip(), None
    left, right = req.split(";", 1)
    return left.strip(), right.strip() or None


def _parse_requirement(req: str) -> Requirement:
    req = req.strip()
    if not req:
        raise ValueError("empty requirement")

    # strip environment marker if present
    req_part, _ = _split_marker(req)
    req_part = req_part.strip()

    extras: set[str] = set()
    if "[" in req_part and "]" in req_part:
        prefix, rest = req_part.split("[", 1)
        extras_part, suffix = rest.split("]", 1)
        req_part = prefix + suffix
        extras = {e.strip() for e in extras_part.split(",") if e.strip()}

    m = re.match(r"^([A-Za-z0-9_.-]+)(.*)$", req_part)
    if not m:
        raise ValueError(f"invalid requirement: {req}")
    name = _normalize_name(m.group(1))
    spec = (m.group(2) or "").strip()
    return Requirement(name=name, spec=spec, extras=extras)


def _parse_spec_set(spec: str) -> list[tuple[str, str]]:
    raw = (spec or "").strip()
    if not raw:
        return []
    # Some metadata uses parentheses: "foo (>=1.0,<2.0)".
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    # drop leading commas/spaces
    raw = raw.lstrip(" ,")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[tuple[str, str]] = []
    for p in parts:
        p = p.strip().strip("() ").strip()
        for op in ("==", ">=", "<=", "!=", "~=", ">", "<"):
            if p.startswith(op):
                out.append((op, p[len(op) :].strip()))
                break
        else:
            raise ValueError(f"unsupported specifier: {p}")
    return out


def _satisfies(version: str, spec_set: list[tuple[str, str]]) -> bool:
    if not spec_set:
        return True

    v_key = _version_key(version)
    for op, rhs in spec_set:
        rhs = rhs.strip()
        if op == "==":
            if rhs.endswith(".*"):
                prefix = rhs[:-2]
                if not version.startswith(prefix + ".") and version != prefix:
                    return False
                continue
            if version != rhs:
                return False
            continue
        if op == "!=":
            if rhs.endswith(".*"):
                prefix = rhs[:-2]
                if version.startswith(prefix + ".") or version == prefix:
                    return False
                continue
            if version == rhs:
                return False
            continue

        rhs_key = _version_key(rhs)
        if op == ">=" and not (v_key >= rhs_key):
            return False
        if op == "<=" and not (v_key <= rhs_key):
            return False
        if op == ">" and not (v_key > rhs_key):
            return False
        if op == "<" and not (v_key < rhs_key):
            return False
        if op == "~=":
            # Compatible release: ~=X.Y means >=X.Y and <X.(Y+1)
            # This is a simplified interpretation good enough for our wheelhouse.
            parts = [int(x) for x in re.findall(r"\d+", rhs)[:2]]
            if len(parts) < 2:
                return False
            lower = _version_key(rhs)
            upper = (parts[0], parts[1] + 1)
            if not (v_key >= lower and v_key < upper):
                return False
    return True


def _eval_marker(marker: str, *, extras: set[str]) -> bool:
    """
    Evaluate PEP508-like markers using a restricted AST evaluator.

    We only support the marker patterns that appear in our dependency tree.
    """

    def _env_for(extra: str) -> dict[str, str]:
        return {
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "python_full_version": sys.version.split(" ")[0],
            "sys_platform": sys.platform,
            "platform_system": "Windows" if sys.platform.startswith("win") else sys.platform,
            "platform_python_implementation": "CPython",
            "implementation_name": "cpython",
            "extra": extra,
        }

    def _to_version_tuple(s: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", s)[:6])

    def _is_version_name(n: str) -> bool:
        return n in {"python_version", "python_full_version"}

    def _eval(node: ast.AST, env: dict[str, str]) -> tuple[Any, str | None]:
        if isinstance(node, ast.Constant):
            return node.value, None
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise ValueError(f"unknown marker name: {node.id}")
            hint = "version" if _is_version_name(node.id) else "str"
            return env[node.id], hint
        if isinstance(node, ast.Tuple):
            vals = [_eval(elt, env)[0] for elt in node.elts]
            return tuple(vals), None
        raise ValueError(f"unsupported marker node: {type(node).__name__}")

    def _eval_bool(expr: ast.AST, env: dict[str, str]) -> bool:
        if isinstance(expr, ast.Expression):
            return _eval_bool(expr.body, env)
        if isinstance(expr, ast.BoolOp):
            if isinstance(expr.op, ast.And):
                return all(_eval_bool(v, env) for v in expr.values)
            if isinstance(expr.op, ast.Or):
                return any(_eval_bool(v, env) for v in expr.values)
            raise ValueError("unsupported bool op")
        if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
            return not _eval_bool(expr.operand, env)
        if isinstance(expr, ast.Compare):
            left_val, left_hint = _eval(expr.left, env)
            cur_val, cur_hint = left_val, left_hint
            for op, comp in zip(expr.ops, expr.comparators):
                right_val, right_hint = _eval(comp, env)
                hint = cur_hint or right_hint
                if hint == "version":
                    a = _to_version_tuple(str(cur_val))
                    b = _to_version_tuple(str(right_val))
                    if isinstance(op, ast.Eq):
                        ok = a == b
                    elif isinstance(op, ast.NotEq):
                        ok = a != b
                    elif isinstance(op, ast.Lt):
                        ok = a < b
                    elif isinstance(op, ast.LtE):
                        ok = a <= b
                    elif isinstance(op, ast.Gt):
                        ok = a > b
                    elif isinstance(op, ast.GtE):
                        ok = a >= b
                    else:
                        raise ValueError("unsupported version compare")
                else:
                    if isinstance(op, ast.Eq):
                        ok = str(cur_val) == str(right_val)
                    elif isinstance(op, ast.NotEq):
                        ok = str(cur_val) != str(right_val)
                    elif isinstance(op, ast.In):
                        ok = str(cur_val) in str(right_val)
                    elif isinstance(op, ast.NotIn):
                        ok = str(cur_val) not in str(right_val)
                    else:
                        raise ValueError("unsupported string compare")
                if not ok:
                    return False
                cur_val, cur_hint = right_val, right_hint
            return True
        if isinstance(expr, ast.Constant) and isinstance(expr.value, bool):
            return bool(expr.value)
        raise ValueError(f"unsupported marker expr: {type(expr).__name__}")

    marker = (marker or "").strip()
    if not marker:
        return True

    tree = ast.parse(marker, mode="eval")
    # If marker doesn't mention `extra`, we can evaluate once with empty extra.
    extras_to_try = sorted(extras) if "extra" in marker else [""]
    if not extras_to_try:
        extras_to_try = [""]
    for ex in extras_to_try:
        env = _env_for(ex)
        if _eval_bool(tree, env):
            return True
    return False


def _compatible_wheel_exists(release_files: list[dict[str, Any]], *, py_tag: str, plat_tag: str) -> bool:
    for f in release_files or []:
        fn = (f.get("filename") or "").strip()
        if not fn.endswith(".whl"):
            continue
        if fn.endswith("-py3-none-any.whl") or fn.endswith("-py2.py3-none-any.whl"):
            return True
        if plat_tag not in fn:
            continue
        if f"{py_tag}-{py_tag}-{plat_tag}.whl" in fn or f"{py_tag}-{py_tag}t-{plat_tag}.whl" in fn:
            return True
        if "-abi3-" in fn and fn.endswith(f"-{plat_tag}.whl"):
            return True
    return False


def _choose_version(package: str, spec_set: list[tuple[str, str]], *, py_tag: str, plat_tag: str) -> str:
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        payload = _fetch_json(url, timeout=30.0)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch package index for {package}: {url}") from e
    releases: dict[str, list[dict[str, Any]]] = payload.get("releases") or {}
    versions = list(releases.keys())

    # Prefer strict numeric releases; fallback to anything if necessary.
    strict = [v for v in versions if _is_strict_release(v)]
    candidates = strict or versions

    viable: list[str] = []
    for v in candidates:
        if not _satisfies(v, spec_set):
            continue
        files = releases.get(v) or []
        if not _compatible_wheel_exists(files, py_tag=py_tag, plat_tag=plat_tag):
            continue
        viable.append(v)

    if not viable:
        raise RuntimeError(f"No compatible wheel found for {package} ({spec_set}) on {py_tag}/{plat_tag}")
    return max(viable, key=_version_key)


@dataclass
class LockedWheel:
    package: str
    version: str
    filename: str
    sha256: str


def _download_locked_wheel(locked: LockedWheel, *, wheels_dir: Path) -> Path:
    wheels_dir.mkdir(parents=True, exist_ok=True)
    dest_path = wheels_dir / locked.filename
    if dest_path.exists():
        got = download_wheel._sha256_file(str(dest_path))  # noqa: SLF001
        if got.lower() != locked.sha256.lower():
            dest_path.unlink(missing_ok=True)
        else:
            return dest_path

    download_wheel.main  # ensure module imported
    # Use the same download logic as `download_wheel.py` but without re-resolving.
    url = f"https://pypi.org/pypi/{locked.package}/{locked.version}/json"
    payload = download_wheel._fetch_json(url, timeout=20.0)  # noqa: SLF001
    urls = payload.get("urls") or []
    wheel_items: list[download_wheel.WheelFile] = []
    for item in urls:
        filename = (item.get("filename") or "").strip()
        if filename != locked.filename:
            continue
        url_item = (item.get("url") or "").strip()
        sha256 = ((item.get("digests") or {}).get("sha256") or "").strip()
        if not url_item or not sha256:
            continue
        wheel_items.append(download_wheel.WheelFile(filename=filename, url=url_item, sha256=sha256))
    if not wheel_items:
        raise RuntimeError(f"Wheel not found on PyPI: {locked.package} {locked.filename}")

    wf = wheel_items[0]
    download_wheel._download(wf.url, str(dest_path), timeout=30.0)  # noqa: SLF001
    got = download_wheel._sha256_file(str(dest_path))  # noqa: SLF001
    if got.lower() != locked.sha256.lower():
        dest_path.unlink(missing_ok=True)
        raise RuntimeError(f"SHA256 mismatch for {dest_path.name}")
    return dest_path


def _lock_package(package: str, version: str, *, py_tag: str, plat_tag: str) -> LockedWheel:
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        payload = _fetch_json(url, timeout=30.0)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch release JSON for {package}=={version}: {url}") from e
    urls = payload.get("urls") or []

    wheel_files: list[download_wheel.WheelFile] = []
    for item in urls:
        filename = (item.get("filename") or "").strip()
        if not filename.endswith(".whl"):
            continue
        url_item = (item.get("url") or "").strip()
        sha256 = ((item.get("digests") or {}).get("sha256") or "").strip()
        if not url_item or not sha256:
            continue
        wheel_files.append(download_wheel.WheelFile(filename=filename, url=url_item, sha256=sha256))

    chosen = download_wheel._pick_best_wheel(wheel_files, py_tag=py_tag, plat_tag=plat_tag)  # noqa: SLF001
    return LockedWheel(package=package, version=version, filename=chosen.filename, sha256=chosen.sha256)


def _read_requirements(path: Path) -> list[Requirement]:
    reqs: list[Requirement] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        reqs.append(_parse_requirement(line))
    return reqs


def _resolve_all(
    root_requirements: list[Requirement],
    *,
    py_tag: str,
    plat_tag: str,
) -> tuple[dict[str, LockedWheel], dict[str, set[str]]]:
    """
    Returns: (locked_wheels_by_name, extras_by_name)
    """
    constraints: dict[str, list[tuple[str, str]]] = {}
    extras_by_name: dict[str, set[str]] = {}
    locked: dict[str, LockedWheel] = {}
    deps_key: dict[str, tuple[str, tuple[str, ...]]] = {}

    queue: list[Requirement] = list(root_requirements)
    while queue:
        req = queue.pop(0)
        name = req.name
        extras_by_name.setdefault(name, set()).update(req.extras)
        constraints.setdefault(name, []).extend(_parse_spec_set(req.spec))

        spec_set = constraints[name]
        cur_locked = locked.get(name)
        if cur_locked is None or not _satisfies(cur_locked.version, spec_set):
            version = _choose_version(name, spec_set, py_tag=py_tag, plat_tag=plat_tag)
            locked[name] = _lock_package(name, version, py_tag=py_tag, plat_tag=plat_tag)

        cur_locked = locked[name]
        cur_extras = extras_by_name.get(name) or set()
        key = (cur_locked.version, tuple(sorted(cur_extras)))
        if deps_key.get(name) == key:
            continue
        deps_key[name] = key

        # Resolve dependencies from requires_dist.
        url = f"https://pypi.org/pypi/{name}/{cur_locked.version}/json"
        try:
            payload = _fetch_json(url, timeout=30.0)
        except Exception:
            # If we cannot read metadata for this release, stop resolving its deps.
            continue
        requires = (payload.get("info") or {}).get("requires_dist") or []
        if not isinstance(requires, list):
            continue

        for dep in requires:
            if not isinstance(dep, str) or not dep.strip():
                continue
            dep_req, marker = _split_marker(dep)
            if marker:
                try:
                    if not _eval_marker(marker, extras=cur_extras):
                        continue
                except Exception:
                    # Be conservative: if marker can't be parsed, skip (best-effort).
                    continue
            try:
                parsed = _parse_requirement(dep_req)
            except Exception:
                continue
            queue.append(parsed)

    return locked, extras_by_name


def _write_lock(path: Path, locked: dict[str, LockedWheel], extras_by_name: dict[str, set[str]]) -> None:
    data = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": download_wheel._platform_tag(),  # noqa: SLF001
        "python_tag": download_wheel._python_tag(),  # noqa: SLF001
        "packages": {
            name: {
                "version": lw.version,
                "wheel": lw.filename,
                "sha256": lw.sha256,
                "extras": sorted(extras_by_name.get(name) or []),
            }
            for name, lw in sorted(locked.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rmtree_strict(path: Path, *, retries: int = 5) -> None:
    """
    Windows-friendly recursive delete with retries and readonly-bit handling.
    """

    def _onerror(func, p: str, _exc) -> None:  # pragma: no cover
        try:
            os.chmod(p, stat.S_IWRITE)
        except OSError:
            pass
        func(p)

    last_err: Exception | None = None
    for attempt in range(retries):
        if not path.exists():
            return
        try:
            shutil.rmtree(path, onerror=_onerror)
            return
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.6 * (2**attempt))
                continue
            raise
    if last_err:
        raise last_err


def _load_lock(path: Path) -> dict[str, LockedWheel]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pkgs = payload.get("packages") or {}
    out: dict[str, LockedWheel] = {}
    for name, item in pkgs.items():
        if not isinstance(item, dict):
            continue
        out[_normalize_name(name)] = LockedWheel(
            package=_normalize_name(name),
            version=str(item.get("version") or ""),
            filename=str(item.get("wheel") or ""),
            sha256=str(item.get("sha256") or ""),
        )
    return out


def _dist_and_version_from_wheel_filename(filename: str) -> tuple[str, str]:
    """
    Wheel filenames use a normalized distribution name where '-' is replaced with '_'.
    The first '-' separates distribution and version.
    """
    base = os.path.basename(filename)
    dist, rest = base.split("-", 1)
    version = rest.split("-", 1)[0]
    return dist, version


def _purge_vendor_metadata(vendor_dir: Path, *, expected_dist_info: set[str], expected_data: set[str]) -> None:
    if not vendor_dir.exists():
        return
    for item in vendor_dir.iterdir():
        if not item.is_dir():
            continue
        name = item.name.lower()
        if name.endswith(".dist-info") and name not in expected_dist_info:
            shutil.rmtree(item, ignore_errors=True)
        elif name.endswith(".data") and name not in expected_data:
            shutil.rmtree(item, ignore_errors=True)


def _fingerprint_lock(locked: dict[str, LockedWheel]) -> str:
    """
    Stable fingerprint for the lock content, used to version vendor directories.
    """
    payload = {
        name: {
            "version": lw.version,
            "wheel": lw.filename,
            "sha256": lw.sha256,
        }
        for name, lw in sorted(locked.items())
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]


def _install_vendor(*, locked: dict[str, LockedWheel], wheels_dir: Path, vendor_dir: Path) -> Path:
    """
    Install vendored deps by extracting wheels into a versioned directory and
    writing `backend/app/_vendor_path.txt` as a pointer to the active vendor.

    This avoids deleting the old vendor dir, which can fail on Windows (WinError 5)
    due to transient file locks or security software.
    """
    vendor_anchor = vendor_dir.resolve()
    app_dir = vendor_anchor.parent
    vendor_root = app_dir / "_vendor_pkgs"
    vendor_state = app_dir / "_vendor_path.txt"

    fingerprint = _fingerprint_lock(locked)
    target_dir = vendor_root / fingerprint
    sentinel = target_dir / "_vendor_complete.json"

    vendor_root.mkdir(parents=True, exist_ok=True)
    if target_dir.exists() and sentinel.exists():
        rel = target_dir.relative_to(app_dir).as_posix()
        vendor_state.write_text(rel, encoding="utf-8")
        return target_dir

    if target_dir.exists() and not sentinel.exists():
        target_dir = vendor_root / f"{fingerprint}-{int(time.time())}"
        sentinel = target_dir / "_vendor_complete.json"

    target_dir.mkdir(parents=True, exist_ok=True)

    wheel_paths: list[Path] = []
    for _name, lw in sorted(locked.items()):
        if not lw.filename:
            continue
        wheel_paths.append(_download_locked_wheel(lw, wheels_dir=wheels_dir))

    for whl in wheel_paths:
        with zipfile.ZipFile(whl) as zf:
            zf.extractall(target_dir)

    sentinel.write_text(
        json.dumps({"fingerprint": fingerprint, "created_at": int(time.time())}, indent=2),
        encoding="utf-8",
    )
    rel = target_dir.relative_to(app_dir).as_posix()
    vendor_state.write_text(rel, encoding="utf-8")
    return target_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sync", "install", "bootstrap"], nargs="?", default="bootstrap")
    parser.add_argument("--requirements", default=str(DEFAULT_REQUIREMENTS))
    parser.add_argument("--wheels", default=str(DEFAULT_WHEELS_DIR))
    parser.add_argument("--vendor", default=str(DEFAULT_VENDOR_DIR))
    parser.add_argument("--lock", default=str(DEFAULT_LOCK_PATH))
    args = parser.parse_args()

    req_path = Path(args.requirements)
    wheels_dir = Path(args.wheels)
    vendor_dir = Path(args.vendor)
    lock_path = Path(args.lock)

    py_tag = download_wheel._python_tag()  # noqa: SLF001
    plat_tag = download_wheel._platform_tag()  # noqa: SLF001

    if args.command in ("sync", "bootstrap"):
        roots = _read_requirements(req_path)
        locked, extras_by_name = _resolve_all(roots, py_tag=py_tag, plat_tag=plat_tag)
        _write_lock(lock_path, locked, extras_by_name)
        print(f"[lock] OK: {lock_path}")

    if args.command in ("install", "bootstrap"):
        if not lock_path.exists():
            raise SystemExit(f"Missing lock file: {lock_path}")
        locked = _load_lock(lock_path)
        target_dir = _install_vendor(locked=locked, wheels_dir=wheels_dir, vendor_dir=vendor_dir)
        print(f"[vendor] OK: {target_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
