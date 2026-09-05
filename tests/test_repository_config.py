import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class RepositoryConfigTests(unittest.TestCase):
    def test_refresh_workflow_has_daily_write_and_validation_steps(self):
        workflow = (ROOT / ".github/workflows/refresh-feed.yml").read_text()
        for text in (
            "schedule:",
            "workflow_dispatch:",
            "contents: write",
            "python -m unittest discover -s tests -v",
            "python generate_feed.py --output feed.xml",
            "python validate_feed.py feed.xml --check-enclosures",
            "git push",
        ):
            self.assertIn(text, workflow)

    def test_readme_has_subscription_url(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn(
            "https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
