"""Prevent accidental reintroduction of the unwanted Technologies brand suffix."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRAND_SURFACES = (
    "service/templates/base.html",
    "service/templates/marketing/home.html",
    "service/templates/marketing/about.html",
    "service/templates/marketing/products.html",
    "service/templates/marketing/contact.html",
    "service/templates/marketing/facerecognition.html",
    "service/templates/marketing/privacy.html",
    "service/templates/marketing/terms.html",
    "service/templates/portal/nav.html",
    "service/templates/portal/dashboard.html",
    "service/templates/owner/reauth.html",
    "service/templates/owner/dashboard.html",
    "service/admin_site.py",
    "src/cloud/ui.py",
    "service/context_processors.py",
)


class NxFiBrandTests(unittest.TestCase):
    def test_user_facing_brand_uses_nxfi_without_technologies_suffix(self):
        for relative in BRAND_SURFACES:
            path = ROOT / relative
            with self.subTest(path=relative):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("NxFi Technologies", content)
                self.assertNotIn("NXFI TECHNOLOGIES", content)

    def test_persian_translation_catalog_uses_the_same_brand(self):
        import json
        catalog = json.loads((ROOT / "src/locales/fa.json").read_text(encoding="utf-8"))
        for source, translation in catalog.items():
            with self.subTest(source=source):
                self.assertNotIn("NxFi Technologies", source)
                self.assertNotIn("NxFi Technologies", translation)
                self.assertNotIn("NXFI TECHNOLOGIES", source)
                self.assertNotIn("NXFI TECHNOLOGIES", translation)


if __name__ == "__main__":
    unittest.main()
