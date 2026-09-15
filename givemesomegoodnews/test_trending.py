"""Tests for the trending topics job and page.

Run with `python3 -m unittest givemesomegoodnews.test_trending`. Nothing here
touches the database or the network: the counting is pure functions over
story dicts, the naming call is given a fake `requests.post`, and the page is
rendered from a snapshot dict.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from . import build_site as bs
from . import syndicate
from . import trending as tr

NOW = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)
_ids = iter(range(1, 10_000))


def story(title, org_id, state="VT", hours_ago=1):
    return {"id": next(_ids), "title": title, "org_id": org_id, "state": state,
            "at": NOW - timedelta(hours=hours_ago), "url": f"https://x.example/{title}"}


def candidate(terms, stories, score=5.0):
    return {"terms": terms, "stories": set(stories), "score": score}


class Terms(unittest.TestCase):
    def test_calendar_numbers_filler_and_foreign_function_words_are_not_terms(self):
        words = tr.headline_words("On Tuesday, 12 schools still close amid heat, dice el alcalde")
        self.assertEqual(words, ["schools", "close", "heat", "dice", "alcalde"])

    def test_a_soft_hyphen_does_not_split_a_word(self):
        self.assertIn("upholds", tr.headline_words("5th Circuit uphold­s Texas ballot rules"))

    def test_terms_are_words_and_adjacent_pairs(self):
        self.assertEqual(tr.terms_of("Hurricane Erin nears coast"),
                         {"hurricane", "erin", "nears", "coast",
                          "hurricane erin", "erin nears", "nears coast"})

    def test_an_ordinal_is_a_term_only_inside_a_phrase(self):
        terms = tr.terms_of("Town marks 25th anniversary")
        self.assertIn("25th anniversary", terms)
        self.assertNotIn("25th", terms)

    def test_a_word_inside_a_matched_phrase_counts_once(self):
        terms = ["mail", "supreme court", "supreme"]
        self.assertEqual(tr.matches(terms, tr.terms_of("Supreme Court takes up a Ten Commandments case")), 1)
        self.assertEqual(tr.matches(terms, tr.terms_of("Supreme Court backs mail order")), 2)


class Folding(unittest.TestCase):
    def test_reprints_fold_into_one_story_credited_to_the_first_to_run_it(self):
        first = story("Nevada sues federal government over Colorado River water cuts", 1, "NV", 5)
        reprint = story("Nevada sues federal government over Colorado River water cuts", 2, "AZ", 2)
        other = story("Arizona farmers brace for smaller Colorado River share", 3, "AZ", 3)
        stories = tr.fold_stories([reprint, other, first])
        self.assertEqual(len(stories), 2)
        folded = next(s for s in stories if len(s["copies"]) == 2)
        self.assertEqual(folded["lead"]["id"], first["id"])

    def test_baseline_counts_newsroom_days_and_ignores_identical_reprints(self):
        base = [
            story("Heat wave grips the valley", 1, hours_ago=48),
            story("Heat wave grips the valley", 2, hours_ago=47),  # reprint
            story("Another heat wave story", 1, hours_ago=46),     # same newsroom, same day
            story("City council meets", 3, hours_ago=24 * 5),
        ]
        expected = tr.baseline_counts(base, {"heat wave", "council"}, days=10)
        self.assertAlmostEqual(expected["heat wave"], 0.1)
        self.assertAlmostEqual(expected["council"], 0.1)

    def test_baseline_scales_to_how_many_newsrooms_publish_today(self):
        base = [story("Heat wave grips the valley", 1, hours_ago=48),
                story("Council meets", 2, hours_ago=48)]
        # Two newsrooms a day in the baseline; twenty publishing today.
        expected = tr.baseline_counts(base, {"heat wave"}, days=1, scale_to=20)
        self.assertAlmostEqual(expected["heat wave"], 10.0)


def day_of(titles_by_org):
    """Fold one day's stories: {org_id: (state, [titles])}."""
    articles = [story(t, org, state) for org, (state, titles) in titles_by_org.items()
                for t in titles]
    return tr.fold_stories(articles)


class Rising(unittest.TestCase):
    def test_a_term_needs_enough_newsrooms_and_states(self):
        stories = day_of({1: ("VT", ["Wildfire smoke settles in"]),
                          2: ("VT", ["Wildfire smoke closes schools"]),
                          3: ("VT", ["Wildfire smoke and your lungs"])})
        self.assertEqual([t["term"] for t in tr.rising_terms(stories, {})], [],
                         "three newsrooms in one state is local, not trending")
        stories = day_of({1: ("VT", ["Wildfire smoke settles in"]),
                          2: ("NH", ["Wildfire smoke closes schools"]),
                          3: ("ME", ["Wildfire smoke and your lungs"])})
        self.assertIn("wildfire smoke", [t["term"] for t in tr.rising_terms(stories, {})])

    def test_a_single_word_needs_a_bigger_jump_than_a_phrase(self):
        stories = day_of({n: (st, [f"Wildfire smoke report {n}"])
                          for n, st in enumerate(["VT", "NH", "ME", "MA", "CT", "RI"], 1)})
        rising = {t["term"] for t in tr.rising_terms(stories, {"wildfire": 1.5, "wildfire smoke": 1.5})}
        self.assertIn("wildfire smoke", rising)  # 6 newsrooms is 4x usual: enough for a phrase
        self.assertNotIn("wildfire", rising)     # but not for a word

    def test_the_same_data_makes_the_same_order(self):
        stories = day_of({n: (st, ["Heat wave hits"]) for n, st in
                          enumerate(["VT", "NH", "ME", "MA"], 1)})
        first = [t["term"] for t in tr.rising_terms(stories, {})]
        self.assertEqual(first, [t["term"] for t in tr.rising_terms(stories, {})])
        self.assertEqual(first[0], "heat wave", "a phrase before its own words at an equal score")


class Grouping(unittest.TestCase):
    def stories(self, n):
        return [{"terms": set(), "lead": None, "copies": []} for _ in range(n)]

    def test_terms_sharing_stories_become_one_topic(self):
        stories = self.stories(10)
        rising = [{"term": "hurricane erin", "stories": {0, 1, 2, 3}, "score": 9},
                  {"term": "erin", "stories": {0, 1, 2, 3, 4}, "score": 8}]
        topics = tr.group_terms(rising, stories)
        self.assertEqual([t["terms"] for t in topics], [["hurricane erin", "erin"]])

    def test_a_small_term_in_a_huge_one_stays_its_own_topic(self):
        stories = self.stories(130)
        rising = [{"term": "trump", "stories": set(range(124)), "score": 20},
                  {"term": "mail voting", "stories": {1, 2, 3, 4, 5, 124, 125, 126}, "score": 10}]
        self.assertEqual(len(tr.group_terms(rising, stories)), 2)

    def test_a_term_almost_wholly_inside_another_merges_whatever_its_size(self):
        stories = self.stories(40)
        rising = [{"term": "mail", "stories": set(range(30)), "score": 16},
                  {"term": "voting order", "stories": {1, 2, 3, 4, 5}, "score": 5}]
        self.assertEqual(len(tr.group_terms(rising, stories)), 1)


class SimilarMerging(unittest.TestCase):
    """Vectors here are 3-d stand-ins for story embeddings."""

    def stories(self, *vectors):
        return [{"terms": set(), "lead": None, "copies": [], "vector": v} for v in vectors]

    def test_one_event_in_different_words_merges_under_the_stronger_topic(self):
        stories = self.stories([1, 0.1, 0], [0.9, 0.2, 0], [1, 0, 0.1], [0.95, 0.1, 0.1])
        topics = [candidate(["never forget"], [2, 3], score=3),
                  candidate(["25th anniversary"], [0, 1], score=9)]
        merged = tr.merge_similar(topics, stories)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["terms"], ["25th anniversary", "never forget"])
        self.assertEqual((merged[0]["stories"], merged[0]["score"]), ({0, 1, 2, 3}, 9))

    def test_different_events_stay_apart(self):
        stories = self.stories([1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0, 0.9, 0.1])
        topics = [candidate(["coal plant"], [0, 1], score=7),
                  candidate(["convention speech"], [2, 3], score=4)]
        self.assertEqual(len(tr.merge_similar(topics, stories)), 2)

    def test_one_shared_headline_does_not_pull_two_topics_together(self):
        # Story 2 carries both "Planned Parenthood" and "general election".
        stories = self.stories([1, 0, 0], [0.9, 0.1, 0], [0.5, 0.5, 0], [0, 1, 0], [0, 0.9, 0.1])
        topics = [candidate(["planned parenthood"], [0, 1, 2]),
                  candidate(["general election"], [2, 3, 4])]
        self.assertEqual(len(tr.merge_similar(topics, stories)), 2)

    def test_a_topic_wholly_inside_another_merges_even_without_vectors(self):
        stories = self.stories(None, None, None)
        topics = [candidate(["mail voting"], [0, 1, 2], score=8), candidate(["ballots"], [1, 2], score=3)]
        self.assertEqual(len(tr.merge_similar(topics, stories)), 1)

    def test_merging_is_transitive_through_the_merged_topic(self):
        # a~b 0.95 merges first; a~c alone is 0.6, but the merged a+b is 0.72 from c.
        stories = self.stories([1, 0, 0], [0.95, 0.31, 0], [0.6, 0.8, 0])
        topics = [candidate(["a"], [0], 9), candidate(["b"], [1], 5), candidate(["c"], [2], 1)]
        merged = tr.merge_similar(topics, stories, threshold=0.7)
        self.assertEqual([m["terms"] for m in merged], [["a", "b", "c"]])


class Naming(unittest.TestCase):
    def setUp(self):
        self.stories = day_of({
            1: ("NV", ["Nevada sues over Colorado River cuts"]),
            2: ("AZ", ["Arizona joins Colorado River fight"]),
            3: ("CA", ["California weighs Colorado River deal"]),
            4: ("UT", ["Utah prison mail goes digital"]),
            5: ("CO", ["Online threat closes Denver school"]),
            6: ("OR", ["Online reviews cost a dentist"]),
            7: ("WA", ["Washington weighs online sales tax"]),
        })
        self.river = candidate(["colorado river"], [0, 1, 2], score=9)
        self.online = candidate(["online"], [4, 5, 6], score=6)
        self.candidates = [self.river, self.online]

    def test_without_a_decision_only_phrase_topics_stand(self):
        topics = tr.finish(tr.apply_decision(self.candidates, self.stories, None), self.stories)
        self.assertEqual([t["label"] for t in topics], ["Colorado River"])

    def test_the_decision_names_merges_and_rejects(self):
        decision = {"topics": [{"groups": ["g1"], "label": "Colorado River water cuts"}],
                    "rejected": ["g2"]}
        topics = tr.finish(tr.apply_decision(self.candidates, self.stories, decision), self.stories)
        self.assertEqual([t["label"] for t in topics], ["Colorado River water cuts"])
        self.assertEqual((topics[0]["n_stories"], topics[0]["n_newsrooms"], topics[0]["n_states"]),
                         (3, 3, 3))

    def test_a_group_the_model_neither_placed_nor_rejected_is_left_out(self):
        decision = {"topics": [{"groups": ["g1"], "label": "Colorado River water cuts"}]}
        topics = tr.finish(tr.apply_decision(self.candidates, self.stories, decision), self.stories)
        self.assertEqual(len(topics), 1)

    def test_the_decision_cannot_invent_groups_reuse_them_or_add_stories(self):
        foreign = self.stories[3]["lead"]["id"]
        decision = {"topics": [
            {"groups": ["g1", "g1", "g9", 7], "label": "Colorado River water cuts",
             "exclude": [foreign, "x", 123456]},
            {"groups": ["g1"], "label": "A second use of g1"},
        ]}
        topics = tr.apply_decision(self.candidates, self.stories, decision)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["stories"], {0, 1, 2})

    def test_exclusions_are_counted_out_and_can_sink_a_topic(self):
        dropped = self.stories[2]["lead"]["id"]
        decision = {"topics": [{"groups": ["g1"], "label": "Colorado River", "exclude": [dropped]}]}
        topics = tr.finish(tr.apply_decision(self.candidates, self.stories, decision), self.stories)
        self.assertEqual(topics, [], "two newsrooms left is under the minimum")

    def test_an_unusable_label_falls_back_to_the_headlines_phrase(self):
        for bad in ["<b>River</b>", "", "x" * 200, 42, None]:
            decision = {"topics": [{"groups": ["g1"], "label": bad}]}
            topics = tr.finish(tr.apply_decision(self.candidates, self.stories, decision),
                               self.stories)
            self.assertEqual(topics[0]["label"], "Colorado River", bad)

    def test_a_garbled_decision_names_nothing_and_keeps_nothing(self):
        for garbage in [{"topics": "all of them"}, {"topics": [None, 3, {"groups": "g1"}]}, {}]:
            self.assertEqual(tr.apply_decision(self.candidates, self.stories, garbage), [])

    def test_the_request_sends_headlines_only(self):
        payload = tr.naming_request(self.candidates, self.stories, ["Old label"])
        self.assertEqual(payload["previous_labels"], ["Old label"])
        self.assertEqual([g["id"] for g in payload["groups"]], ["g1", "g2"])
        self.assertEqual(set(payload["groups"][0]["headlines"][0]), {"id", "title"})

    def test_surface_form_keeps_names_and_lowers_ordinary_phrases(self):
        self.assertEqual(tr.surface_form("fema", ["FEMA cuts grants", "Where FEMA went"]), "FEMA")
        self.assertEqual(tr.surface_form("school shooting",
                                         ["School Shooting In Ohio", "School Shooting Suspect",
                                          "After the school shooting, a vigil"]),
                         "School shooting")
        self.assertEqual(tr.surface_form("trump mail", ["Trump’s mail order upheld"]), "Trump mail")
        self.assertEqual(tr.surface_form("marks since", ["Town marks 25 years since the attacks"]),
                         "Marks 25 years since")
        self.assertEqual(tr.surface_form("groups sue", []), "Groups sue")


class Calling(unittest.TestCase):
    def response(self, content, status=200):
        r = mock.Mock(status_code=status)
        r.json.return_value = {"choices": [{"message": {"content": content}}]}
        r.raise_for_status.side_effect = None if status == 200 else tr.requests.HTTPError("503")
        return r

    def test_asks_for_json_without_thinking(self):
        with mock.patch.object(tr.requests, "post", return_value=self.response('{"topics": []}')) as post:
            self.assertEqual(tr.ask_deepseek({"groups": []}, "sk-test"), {"topics": []})
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertIn("json", body["messages"][0]["content"])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer sk-test")

    def test_retries_empty_content_once(self):
        replies = [self.response(""), self.response('{"topics": []}')]
        with mock.patch.object(tr.requests, "post", side_effect=replies) as post, \
                mock.patch("sys.stderr"):
            self.assertEqual(tr.ask_deepseek({}, "sk-test"), {"topics": []})
        self.assertEqual(post.call_count, 2)

    def test_failure_is_none_and_never_prints_the_key(self):
        errors = []
        with mock.patch.object(tr.requests, "post", return_value=self.response("", 503)), \
                mock.patch("sys.stderr") as err:
            err.write.side_effect = errors.append
            self.assertIsNone(tr.ask_deepseek({}, "sk-secret-value"))
        self.assertTrue(errors)
        self.assertNotIn("sk-secret-value", "".join(errors))

    def test_non_json_is_none(self):
        with mock.patch.object(tr.requests, "post", return_value=self.response("Sure! Here")), \
                mock.patch("sys.stderr"):
            self.assertIsNone(tr.ask_deepseek({}, "sk-test"))


def snapshot(**over):
    item = {"id": 1, "url": "https://river.example/a", "title": "Nevada sues over river cuts",
            "published_at": NOW, "org_name": "The Nevada Independent", "slug": "the-nevada-independent",
            "org_url": "https://thenevadaindependent.com/", "state": "NV"}
    reprint = dict(item, id=2, org_name="Arizona Mirror", slug="arizona-mirror", state="AZ")
    snap = {"generated_at": NOW, "window_hours": 24, "baseline_days": 14,
            "labeler": "deepseek-flash",
            "topics": [{"label": "Colorado River <cuts>", "search": "Colorado River",
                        "n_stories": 1, "n_newsrooms": 2, "n_states": 2,
                        "stories": bs.collapse_duplicates([item, reprint])}]}
    snap.update(over)
    return snap


class Page(unittest.TestCase):
    def test_before_the_first_snapshot(self):
        self.assertIn("first list", bs.render_trending(None))

    def test_topic_counts_escaping_and_reprints(self):
        html = bs.render_trending(snapshot(), now=NOW)
        self.assertIn("<h2>Colorado River &lt;cuts&gt;</h2>", html)
        self.assertIn("1 story &middot; 2 newsrooms &middot; 2 states", html)
        self.assertIn("also in Arizona Mirror", html)
        self.assertIn('href="orgs/the-nevada-independent.html"', html)
        self.assertIn("DeepSeek (deepseek-flash)", html)
        self.assertNotIn("running late", html)
        self.assertNotIn("/search?", html, "no search link when every story is shown")

    def test_says_how_topics_were_named_without_a_model(self):
        html = bs.render_trending(snapshot(labeler="terms"), now=NOW)
        self.assertNotIn("DeepSeek", html)
        self.assertIn("phrase its headlines share", html)

    def test_points_to_search_for_the_rest(self):
        snap = snapshot()
        stories = [dict(snap["topics"][0]["stories"][0], id=10 + n, title=f"Distinct {n} story words")
                   for n in range(bs.TRENDING_STORIES + 2)]
        snap["topics"][0]["stories"] = bs.collapse_duplicates(stories)
        html = bs.render_trending(snap, now=NOW)
        self.assertIn("2 more stories", html)
        self.assertIn('href="/search?q=%22Colorado+River%22"', html)

    def test_says_when_the_list_is_late(self):
        html = bs.render_trending(snapshot(), now=NOW + timedelta(hours=bs.TRENDING_STALE_HOURS + 1))
        self.assertIn("running late", html)

    def test_nothing_rising(self):
        self.assertIn("Nothing is running", bs.render_trending(snapshot(topics=[]), now=NOW))

    def test_in_the_menu_and_kept_from_crawlers(self):
        self.assertIn('href="trending.html"', bs.menu())
        self.assertIn("Disallow: /trending.html", syndicate.ROBOTS)


if __name__ == "__main__":
    unittest.main()
