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
    "service/templates/owner/dashboard.html",
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


if __name__ == "__main__":
    unittest.main()
