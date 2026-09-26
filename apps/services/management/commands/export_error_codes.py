"""
python manage.py export_error_codes            # يكتب ERROR_CODES.md
python manage.py export_error_codes --check    # يفشل إن كان الملف قديمًا

قائمة كل رموز الأخطاء (`code` في ErrorOut) مستخرجة من الكود نفسه — لا تُكتب
يدويًا فلا تتقادم. التطبيق يترجم الرسائل بالـcode (طلب فريق التطبيق B10).

الاستخراج (AST، بلا تشغيل الكود):
  1) كل كلاس فيه `code = "..."` → الرمز ووصفه (أول سطر من docstring).
  2) في ملفات api/: كل `except X as exc: return _error(409, exc.code, ...)`
     وكل `if isinstance(exc, X): return _error(403, exc.code, ...)` → حالة
     HTTP لكل كلاس.
  3) الرموز الحرفية: `_error(404, "job_not_found", ...)` و `AuthzError("...")`.
"""

import ast
import pathlib
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

OUTPUT = "ERROR_CODES.md"
ERROR_HELPERS = {"_error", "error", "_auth_error_status"}


def _names(node):
    """أسماء الكلاسات في except X / except (A, B) / isinstance(exc, (A, B))."""
    if node is None:
        return []
    if isinstance(node, ast.Tuple):
        return [n for elt in node.elts for n in _names(elt)]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Name):
        return [node.id]
    return []


def _returned_status(body):
    """حالة HTTP من `return _error(STATUS, exc.code...)` أو `return STATUS, {...}` داخل جسم."""
    statuses = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", getattr(value.func, "attr", "")) in ERROR_HELPERS:
            if value.args and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, int):
                statuses.add(value.args[0].value)
        elif isinstance(value, ast.Tuple) and value.elts and isinstance(value.elts[0], ast.Constant):
            if isinstance(value.elts[0].value, int):
                statuses.add(value.elts[0].value)
    return statuses


def collect(base):
    codes = {}  # code -> {"statuses": set, "meaning": str, "sources": set}
    class_code = {}
    class_status = defaultdict(set)
    class_bases = {}
    class_attr_status = {}

    def entry(code):
        return codes.setdefault(code, {"statuses": set(), "meaning": "", "sources": set()})

    for path in sorted(base.glob("apps/**/*.py")) + sorted(base.glob("config/**/*.py")):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        rel = path.relative_to(base).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            # (1) كلاسات بـ code = "..."
            if isinstance(node, ast.ClassDef):
                class_bases.setdefault(node.name, _names(ast.Tuple(elts=node.bases)))
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and any(getattr(t, "id", "") == "status" for t in stmt.targets)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, int)
                    ):
                        class_attr_status[node.name] = stmt.value.value
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and any(getattr(t, "id", "") == "code" for t in stmt.targets)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)
                    ):
                        code = stmt.value.value
                        class_code[node.name] = code
                        e = entry(code)
                        doc = ast.get_docstring(node) or ""
                        first = doc.strip().splitlines()[0] if doc.strip() else ""
                        # الجمهور فريق التطبيق: الوصف الإنجليزي وحده
                        if first and first.isascii() and not e["meaning"]:
                            e["meaning"] = first
                        e["sources"].add(rel)

            # (2) except X as exc: return _error(STATUS, exc.code)
            if isinstance(node, ast.ExceptHandler):
                for status in _returned_status(node.body):
                    for name in _names(node.type):
                        class_status[name].add(status)

            # (2b) if isinstance(exc, X): return _error(STATUS, ...)
            if isinstance(node, ast.If) and isinstance(node.test, ast.Call):
                if getattr(node.test.func, "id", "") == "isinstance" and len(node.test.args) == 2:
                    for status in _returned_status(node.body):
                        for name in _names(node.test.args[1]):
                            class_status[name].add(status)

            # (3) رموز حرفية
            if isinstance(node, ast.Call):
                func = getattr(node.func, "id", getattr(node.func, "attr", ""))
                args = node.args
                if func in ERROR_HELPERS and len(args) >= 2 and isinstance(args[1], ast.Constant) \
                        and isinstance(args[1].value, str) and isinstance(args[0], ast.Constant):
                    e = entry(args[1].value)
                    e["statuses"].add(args[0].value)
                    e["sources"].add(rel)
                if func == "AuthzError" and args and isinstance(args[0], ast.Constant):
                    e = entry(args[0].value)
                    status = 403
                    for kw in node.keywords:
                        if kw.arg == "status" and isinstance(kw.value, ast.Constant):
                            status = kw.value.value
                    e["statuses"].add(status)
                    e["sources"].add(rel)

    def statuses_for(name, seen=()):
        """حالات الكلاس نفسه، أو أقرب أصل له حالة (except Base يلتقط الفروع)."""
        own = set(class_status.get(name, set()))
        if name in class_attr_status:
            own.add(class_attr_status[name])
        if own or name in seen:
            return own
        for base in class_bases.get(name, []):
            inherited = statuses_for(base, seen + (name,))
            if inherited:
                return inherited
        return set()

    for name, code in class_code.items():
        codes[code]["statuses"] |= statuses_for(name)

    # يولّده معالج مركزي لا كلاس
    e = entry("validation_error")
    e["statuses"].add(422)
    e["meaning"] = e["meaning"] or "The request body, query or path failed schema validation; see `errors[]`."
    e["sources"].add("config/urls.py")
    return codes


def render(codes):
    lines = [
        "# API error codes",
        "",
        "Every handled error response has the body `{\"code\": \"...\", \"detail\": \"...\"}` "
        "(auth endpoints may add `retry_after_seconds`; `validation_error` adds `errors[]`). "
        "`detail` is English and may change; **`code` is stable** — translate by `code`.",
        "",
        "`401` from a missing or invalid token has no `code` (framework response: "
        "`{\"detail\": \"Unauthorized\"}`) — treat any `401` as \"log in again\" "
        "(or refresh the token first).",
        "",
        "Generated from the source by `python manage.py export_error_codes` — do not edit by hand. "
        "A test fails when this file is out of date, so a new or renamed code always shows up in review.",
        "",
        f"**{len(codes)} codes.**",
        "",
        "| Code | HTTP | Meaning | Defined in |",
        "| --- | --- | --- | --- |",
    ]
    for code in sorted(codes):
        e = codes[code]
        statuses = ", ".join(str(s) for s in sorted(e["statuses"])) or "—"
        meaning = e["meaning"].replace("|", "\\|")
        sources = ", ".join(sorted(e["sources"]))
        lines.append(f"| `{code}` | {statuses} | {meaning} | {sources} |")
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = "Write ERROR_CODES.md with every API error code, extracted from the source."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Fail if the file is out of date.")

    def handle(self, *args, **options):
        base = pathlib.Path(settings.BASE_DIR)
        content = render(collect(base))
        target = base / OUTPUT
        if options["check"]:
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if current != content:
                raise CommandError(f"{OUTPUT} is out of date — run: python manage.py export_error_codes")
            self.stdout.write(f"{OUTPUT} is up to date.")
            return
        target.write_text(content, encoding="utf-8")
        self.stdout.write(f"Wrote {OUTPUT} ({content.count(chr(10)) - 12} codes).")
