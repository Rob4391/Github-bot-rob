import unittest

from app.semver import bump_semver, parse_semver


class TestSemver(unittest.TestCase):
    def test_parse_valid(self) -> None:
        semver = parse_semver("1.2.3")
        self.assertEqual((semver.major, semver.minor, semver.patch), (1, 2, 3))

    def test_parse_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_semver("1.2")

    def test_bump_patch(self) -> None:
        self.assertEqual(bump_semver("1.2.3", "patch"), "1.2.4")

    def test_bump_minor(self) -> None:
        self.assertEqual(bump_semver("1.2.3", "minor"), "1.3.0")

    def test_bump_major(self) -> None:
        self.assertEqual(bump_semver("1.2.3", "major"), "2.0.0")


if __name__ == "__main__":
    unittest.main()

