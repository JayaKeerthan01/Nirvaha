import unittest

from agents.citizen_chat_agent import find_zone, _match_faq, _rule_based_answer

ZONES = [
    {"name": "HSR Layout"},
    {"name": "Koramangala"},
    {"name": "Bellandur"},
    {"name": "BTM Layout"},
    {"name": "Electronic City"},
]


class TestFindZone(unittest.TestCase):
    def test_full_name_match(self):
        z = find_zone("what's the risk in Koramangala right now", ZONES)
        self.assertEqual(z["name"], "Koramangala")

    def test_case_insensitive(self):
        z = find_zone("RISK IN BELLANDUR", ZONES)
        self.assertEqual(z["name"], "Bellandur")

    def test_first_word_fallback_match(self):
        # "hsr" alone should still resolve to "HSR Layout" via the
        # first-word fallback, not just an exact full-name match.
        z = find_zone("nearest hospital to hsr", ZONES)
        self.assertEqual(z["name"], "HSR Layout")

    def test_no_match_returns_none(self):
        z = find_zone("what's the weather like today", ZONES)
        self.assertIsNone(z)

    def test_multi_word_zone_first_word_match(self):
        z = find_zone("evacuation route from electronic", ZONES)
        self.assertEqual(z["name"], "Electronic City")

    def test_typo_resolves_via_fuzzy_match(self):
        # "Koramangla" (missing the second 'a') used to fail outright and
        # fall through to the generic "I don't understand" fallback.
        z = find_zone("what's the risk in Koramangla", ZONES)
        self.assertEqual(z["name"], "Koramangala")

    def test_minor_typo_in_first_word_resolves(self):
        z = find_zone("evacuation route from Bellandurr", ZONES)
        self.assertEqual(z["name"], "Bellandur")

    def test_short_word_does_not_trigger_fuzzy_match(self):
        # Fuzzy matching only kicks in for words of length >= 4, so short
        # common words can't spuriously resolve to a zone name.
        z = find_zone("is it ok to go out", ZONES)
        self.assertIsNone(z)

    def test_ambiguous_first_word_is_not_guessed(self):
        # If two zones ever share a first word (possible once admins can
        # add zones dynamically via /admin/zones), guessing which one was
        # meant would silently answer about the wrong one some of the
        # time. Ambiguous should mean "no confident match", not a guess.
        ambiguous_zones = ZONES + [{"name": "Electronic Hub"}]
        z = find_zone("what's happening in electronic", ambiguous_zones)
        self.assertIsNone(z)

    def test_full_name_still_resolves_even_with_ambiguous_first_word(self):
        # The ambiguity guard only applies to the loose first-word
        # fallback — an exact full-name mention always wins outright.
        ambiguous_zones = ZONES + [{"name": "Electronic Hub"}]
        z = find_zone("risk in Electronic Hub", ambiguous_zones)
        self.assertEqual(z["name"], "Electronic Hub")

    def test_transposed_letters_still_resolve(self):
        z = find_zone("nearest hospital to Elcetronic City", ZONES)
        self.assertEqual(z["name"], "Electronic City")

    def test_nonsense_word_does_not_spuriously_match(self):
        z = find_zone("what about xyzzyplugh", ZONES)
        self.assertIsNone(z)


class TestMatchFaq(unittest.TestCase):
    """_match_faq is a pure function (no DB, no live agents) so these run
    without any app setup — the same property find_zone's tests rely on."""

    def test_emergency_kit_topic_matches(self):
        topic_id, ans = _match_faq("what should i pack in an emergency kit")
        self.assertEqual(topic_id, "emergency_kit")
        self.assertIn("water", ans.lower())

    def test_water_purification_topic_matches(self):
        topic_id, ans = _match_faq("how do i purify water during a flood")
        self.assertEqual(topic_id, "water_purification")
        self.assertIn("boil", ans.lower())

    def test_earthquake_topic_notes_not_predicted(self):
        topic_id, ans = _match_faq("earthquake safety tips")
        self.assertEqual(topic_id, "earthquake_safety")
        self.assertIn("aren't predicted", ans)

    def test_unrelated_question_has_no_faq_match(self):
        topic_id, ans = _match_faq("what time is it")
        self.assertIsNone(topic_id)
        self.assertIsNone(ans)


class TestRuleBasedMetaAnswers(unittest.TestCase):
    """Meta/small-talk branches return before touching the DB, so these
    can run against an empty zones list with no live agents involved."""

    def test_greeting(self):
        ans, source = _rule_based_answer("hi", [])
        self.assertIn("ask me", ans.lower())
        self.assertEqual(source, "assistant")

    def test_thanks(self):
        ans, source = _rule_based_answer("thanks a lot", [])
        self.assertIn("welcome", ans.lower())
        self.assertEqual(source, "assistant")

    def test_goodbye(self):
        ans, source = _rule_based_answer("bye", [])
        self.assertIn("safe", ans.lower())
        self.assertEqual(source, "assistant")

    def test_capabilities_question(self):
        ans, source = _rule_based_answer("what can you do?", [])
        self.assertIn("evacuation", ans.lower())
        self.assertIn("emergency", ans.lower())
        self.assertEqual(source, "assistant")

    def test_about_app_question(self):
        ans, source = _rule_based_answer("how does this app work", [])
        self.assertIn("nirvaha", ans.lower())
        self.assertEqual(source, "assistant")


if __name__ == "__main__":
    unittest.main()
