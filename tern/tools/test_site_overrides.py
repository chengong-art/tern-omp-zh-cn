import unittest

from site_overrides import skip_translation


class SiteOverridesTests(unittest.TestCase):
    def test_null_override_skips_only_its_exact_copy(self):
        overrides = {(0x1000, 4): None, (0x2000, 4): "自动"}

        self.assertTrue(skip_translation(overrides, 0x1000, 4))
        self.assertFalse(skip_translation(overrides, 0x2000, 4))

    def test_missing_override_and_empty_map_do_not_skip(self):
        self.assertFalse(skip_translation({}, 0x1000, 4))
        self.assertFalse(skip_translation({(0x1000, 4): "自动"}, 0x2000, 4))

    def test_invalid_override_is_rejected(self):
        for value in ("", False, []):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    skip_translation({(0x1000, 4): value}, 0x1000, 4)


if __name__ == "__main__":
    unittest.main()
