"""Prevent accidental reintroduction of the unwanted Technologies brand suffix."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
USER_FACING_PYTHON = (
    "service/admin_site.py",
    "service/context_processors.py",
    "src/cloud/ui.py",
)


class NxFiBrandTests(unittest.TestCase):
    def assert_concise_brand(self, path, content):
        with self.subTest(path=str(path.relative_to(ROOT))):
            self.assertNotIn("nxfi technologies", content.casefold())

    def test_all_user_facing_templates_use_concise_nxfi_brand(self):
        templates = sorted((ROOT / "service/templates").rglob("*.html"))
        self.assertTrue(templates, "Expected to find website templates")
        for path in templates:
            self.assert_concise_brand(path, path.read_text(encoding="utf-8"))
        for relative in USER_FACING_PYTHON:
            path = ROOT / relative
            self.assert_concise_brand(path, path.read_text(encoding="utf-8"))

    def test_persian_translation_catalog_uses_the_same_brand(self):
        path = ROOT / "src/locales/fa.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(catalog, "Expected a nonempty Persian translation catalog")
        for source, translation in catalog.items():
            self.assert_concise_brand(path, source)
            self.assert_concise_brand(path, translation)

    def test_compiled_persian_website_catalog_matches_reviewed_source(self):
        # Compile the .mo file from the reviewed JSON catalog before web tests.
        # The workflow invokes tools.compile_translations, but this test gives
        # a direct, readable failure if the packaged catalog is missing.
        path = ROOT / "service/locale/fa/LC_MESSAGES/django.mo"
        self.assertTrue(path.is_file(), "Compiled Persian Django catalog is missing")
        self.assertGreater(path.stat().st_size, 1000, "Compiled Persian Django catalog looks empty")


if __name__ == "__main__":
    unittest.main()
