"""Translations without GNU gettext (which Windows doesn't have), using Django's template reader + Babel.

    manage.py translations extract   # find every translatable string -> locale/<lang>/LC_MESSAGES/django.po
    manage.py translations compile   # .po -> .mo (what Django loads)
    manage.py translations stats     # how much is translated

Existing translations are kept when extracting; strings that disappeared from the code are dropped.
"""
import io
import os
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.extract import extract
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation.template import templatize

KEYWORDS = {
    "_": None, "N_": None, "gettext": None, "gettext_lazy": None, "gettext_noop": None,
    "ngettext": (1, 2), "ngettext_lazy": (1, 2),
    "pgettext": ((1, "c"), 2), "pgettext_lazy": ((1, "c"), 2), "npgettext": ((1, "c"), 2, 3), "npgettext_lazy": ((1, "c"), 2, 3),
}
SKIP_DIRS = {"venv", ".venv", ".git", "node_modules", "media", "staticfiles", "static", "migrations", "tests", "__pycache__", "locale"}


def _languages():
    return [code for code, _name in settings.LANGUAGES if code != settings.LANGUAGE_CODE]


def _po_path(language):
    return Path(settings.BASE_DIR) / "locale" / language / "LC_MESSAGES" / "django.po"


def _sources(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        parts = Path(dirpath).parts
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if name.endswith(".py") and name != "tests.py" and "management" not in parts:
                yield path, False
            elif name.endswith((".html", ".txt")) and "templates" in parts:
                yield path, True


def build_template():
    root = Path(settings.BASE_DIR)
    catalog = Catalog(project=settings.APP_NAME, charset="utf-8")
    for path, is_template in _sources(root):
        text = path.read_text(encoding="utf-8")
        if is_template:
            if "trans" not in text:
                continue
            # templatize() writes gettext() calls on the template's own lines; drop indentation so it tokenizes as Python.
            text = "\n".join(line.lstrip() for line in templatize(text, origin=str(path)).splitlines())
        elif "gettext" not in text and "_(" not in text:
            continue
        for lineno, message, comments, context in extract("python", io.BytesIO(text.encode("utf-8")), keywords=KEYWORDS,
                                                          comment_tags=("Translators:",)):
            singular = message[0] if isinstance(message, tuple) else message
            if not singular:
                continue
            flags = ("python-format",) if "%(" in singular or "%s" in singular else ()
            catalog.add(message, locations=[(path.relative_to(root).as_posix(), lineno)], auto_comments=comments,
                        context=context, flags=flags)
    return catalog


class Command(BaseCommand):
    help = "Extract, compile or count translations (no GNU gettext needed)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["extract", "compile", "stats"])

    def handle(self, *args, action, **options):
        getattr(self, action)()

    def extract(self):
        template = build_template()
        for language in _languages():
            path = _po_path(language)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                with path.open("rb") as fh:
                    catalog = read_po(fh, locale=language)
            else:
                catalog = Catalog(locale=language, project=settings.APP_NAME, charset="utf-8")
            catalog.update(template, no_fuzzy_matching=True)
            with path.open("wb") as fh:
                write_po(fh, catalog, width=0, ignore_obsolete=True, include_previous=False)
            self.stdout.write(f"{language}: {len(template)} strings -> {path.relative_to(settings.BASE_DIR)}")

    def compile(self):
        for language in _languages():
            path = _po_path(language)
            if not path.exists():
                raise CommandError(f"{path} doesn't exist; run `translations extract` first.")
            with path.open("rb") as fh:
                catalog = read_po(fh, locale=language)
            problems = [m for m in catalog if m.id and m.string and _placeholders(m.id) != _placeholders(m.string)]
            if problems:
                for message in problems[:20]:
                    self.stderr.write(f"Placeholder mismatch: {message.id!r} -> {message.string!r}")
                raise CommandError(f"{len(problems)} translations have wrong %(...)s placeholders.")
            with path.with_suffix(".mo").open("wb") as fh:
                write_mo(fh, catalog, use_fuzzy=False)
            self.stdout.write(f"{language}: compiled {path.with_suffix('.mo').relative_to(settings.BASE_DIR)}")

    def stats(self):
        for language in _languages():
            with _po_path(language).open("rb") as fh:
                catalog = read_po(fh, locale=language)
            total = sum(1 for m in catalog if m.id)
            done = sum(1 for m in catalog if m.id and (all(m.string) if isinstance(m.string, tuple) else m.string))
            self.stdout.write(f"{language}: {done}/{total} translated")


def _placeholders(value):
    import re

    texts = value if isinstance(value, tuple) else (value,)
    return sorted({p for text in texts for p in re.findall(r"%\(\w+\)s", text or "")})
