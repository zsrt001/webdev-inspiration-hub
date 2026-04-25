# Offline Wheels

This folder is for offline dependency wheels (no internet / no PyPI access).

## Pillow (Python 3.13 + Windows x64)

Target environment:
- Python: `3.13` (cp313)
- Platform: `win_amd64`

1. Download on a machine with internet:

```powershell
python -m pip download pillow==11.0.0 --only-binary=:all: --platform win_amd64 --python-version 313 --implementation cp --abi cp313 -d backend\\wheels
```

2. Install into vendored site-packages (repo local):

```powershell
powershell -ExecutionPolicy Bypass -File backend\\scripts\\install_pillow.ps1
```

After that, the backend will pick it up automatically via `backend/app/__init__.py` vendoring hook.

