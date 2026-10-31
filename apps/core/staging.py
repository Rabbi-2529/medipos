"""Keep a checked upload on disk between "preview" and "confirm" (too big for the session)."""
import json
import uuid
from pathlib import Path

from django.conf import settings


def _path(tenant, token):
    folder = Path(settings.MEDIA_ROOT) / "imports" / str(tenant.pk)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{token}.json"


def stage(request, key, payload):
    """Save `payload` for this user's next step; replaces any earlier upload under `key`."""
    clear(request, key)
    token = uuid.uuid4().hex
    _path(request.tenant, token).write_text(json.dumps(payload, default=str), encoding="utf-8")
    request.session[key] = {"token": token}
    return token


def load(request, key):
    staged = request.session.get(key)
    if not staged:
        return None
    path = _path(request.tenant, staged["token"])
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def clear(request, key):
    staged = request.session.pop(key, None)
    if staged:
        _path(request.tenant, staged["token"]).unlink(missing_ok=True)
