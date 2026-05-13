from __future__ import annotations

from patchguard.models import ChangedFile
from patchguard.services.function_extractor import FunctionExtractor


def test_parse_changed_lines_from_patch_hunk() -> None:
    patch = """@@ -5,1 +5,1 @@
-    value = x + 1
+    value = x + 2
"""

    assert FunctionExtractor.parse_changed_lines(patch) == [5]


def test_extracts_changed_top_level_function(tmp_path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text(
        """def untouched():
    return 1

def target(x):
    value = x + 2
    return value
""",
        encoding="utf-8",
    )
    changed_file = ChangedFile(
        filename="src/app.py",
        status="modified",
        patch="""@@ -5,1 +5,1 @@
-    value = x + 1
+    value = x + 2
""",
    )

    changed_functions = FunctionExtractor().extract_changed_functions(tmp_path, [changed_file])

    assert len(changed_functions) == 1
    assert changed_functions[0].qualified_name == "target"
    assert changed_functions[0].symbol_type == "function"
    assert changed_functions[0].start_line == 4
    assert changed_functions[0].end_line == 6
    assert changed_functions[0].changed_lines == [5]
    assert "def target" in changed_functions[0].source_code


def test_extracts_changed_class_method_with_qualified_name(tmp_path) -> None:
    source = tmp_path / "src" / "greeter.py"
    source.parent.mkdir()
    source.write_text(
        """class Greeter:
    def greet(self, name):
        return f"hello {name}"
""",
        encoding="utf-8",
    )
    changed_file = ChangedFile(
        filename="src/greeter.py",
        status="modified",
        patch="""@@ -3,1 +3,1 @@
-        return f"hi {name}"
+        return f"hello {name}"
""",
    )

    changed_functions = FunctionExtractor().extract_changed_functions(tmp_path, [changed_file])

    assert len(changed_functions) == 1
    assert changed_functions[0].qualified_name == "Greeter.greet"
    assert changed_functions[0].symbol_type == "method"
    assert changed_functions[0].changed_lines == [3]


def test_syntax_error_falls_back_to_file_context(tmp_path) -> None:
    source = tmp_path / "src" / "broken.py"
    source.parent.mkdir()
    source.write_text("def broken(:\n    pass\n", encoding="utf-8")
    changed_file = ChangedFile(
        filename="src/broken.py",
        status="modified",
        patch="""@@ -1,1 +1,1 @@
-def broken(:
+def broken(name):
""",
    )

    changed_functions = FunctionExtractor().extract_changed_functions(tmp_path, [changed_file])

    assert len(changed_functions) == 1
    fallback = changed_functions[0]
    assert fallback.qualified_name == "<file>"
    assert fallback.symbol_type == "file"
    assert fallback.changed_lines == [1]
    assert fallback.fallback is True
    assert fallback.parse_error
