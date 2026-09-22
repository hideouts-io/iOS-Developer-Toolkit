from __future__ import annotations

import unittest

from ios_developer_toolkit.action_palette import (
    ActionPaletteError,
    action_palette_entry,
    filter_action_palette,
    validate_action_palette,
)


class ActionPaletteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = (
            action_palette_entry(
                "navigate:Live Logs",
                "Open Live Logs",
                "Workspace",
                "Open independent logging streams.",
                ("unified", "syslog", "oslog"),
            ),
            action_palette_entry(
                "preset:lockdown",
                "Choose Lockdown overview",
                "Guided command",
                "Prepare the read-only Lockdown preset for review.",
                ("device", "pairing"),
            ),
            action_palette_entry(
                "utility:session-activity",
                "Open Session Activity",
                "Utility",
                "Review typed operation results.",
                ("history", "manifest"),
            ),
        )

    def test_filters_all_terms_across_titles_summaries_and_keywords(self) -> None:
        self.assertEqual(
            tuple(entry.identifier for entry in filter_action_palette(self.entries, "device pairing")),
            ("preset:lockdown",),
        )
        self.assertEqual(
            tuple(entry.identifier for entry in filter_action_palette(self.entries, "manifest")),
            ("utility:session-activity",),
        )

    def test_ranks_title_matches_before_keyword_matches(self) -> None:
        matching = filter_action_palette(self.entries, "live")

        self.assertEqual(matching[0].identifier, "navigate:Live Logs")

    def test_rejects_duplicate_or_incomplete_entries(self) -> None:
        with self.assertRaises(ActionPaletteError):
            validate_action_palette((self.entries[0], self.entries[0]))
        with self.assertRaises(ActionPaletteError):
            action_palette_entry("", "Missing", "Utility", "Invalid.", ())


if __name__ == "__main__":
    unittest.main()
