"""Jev as the fourth subject layer: what is sent, what is kept."""

import unittest
from unittest import mock

from . import jev


def _reply(choice, probabilities, status=200, tokens=480):
    r = mock.Mock()
    r.status_code = status
    r.raise_for_status = mock.Mock()
    r.json.return_value = {"answers": {"subject": {"type": "choice", "choice": choice,
                                                   "confidence": 0.9, "probabilities": probabilities}},
                           "usage": {"input_tokens": tokens, "output_tokens": 3}}
    return r


class Ask(unittest.TestCase):
    def test_takes_a_clear_subject(self):
        http = mock.Mock(post=mock.Mock(return_value=_reply("Sports", {"Sports": 0.92, "News": 0.05})))
        self.assertEqual(jev.ask("Tigers win", "The team...", "k", http), ("Sports", 0.92, 480))
        body = http.post.call_args.kwargs["json"]
        self.assertEqual(body["state"], {"headline": "Tigers win", "summary": "The team..."})
        self.assertEqual(set(body["questions"]["subject"]["criteria"]), set(jev.CRITERIA))

    def test_leaves_a_split_decision_untagged(self):
        http = mock.Mock(post=mock.Mock(return_value=_reply("News", {"News": 0.4, "Politics": 0.38})))
        self.assertEqual(jev.ask("t", "s", "k", http)[0], None)

    def test_none_of_these_is_untagged(self):
        http = mock.Mock(post=mock.Mock(return_value=_reply(jev.NONE, {jev.NONE: 0.97})))
        subject, p, tokens = jev.ask("Obituary: Jane Doe", "", "k", http)
        self.assertIsNone(subject)
        self.assertEqual(tokens, 480)

    def test_backs_off_on_a_rate_limit(self):
        http = mock.Mock(post=mock.Mock(side_effect=[
            _reply("", {}, status=429), _reply("Food", {"Food": 0.8})]))
        with mock.patch.object(jev.time, "sleep") as sleep:
            self.assertEqual(jev.ask("t", "s", "k", http)[0], "Food")
        sleep.assert_called_once()


class Judge(unittest.TestCase):
    def test_a_failed_request_is_reported_not_raised(self):
        with mock.patch.object(jev, "ask", side_effect=[("Arts", 0.7, 500), RuntimeError("down")]):
            out = list(jev.judge([(1, "a", ""), (2, "b", "")], "k", workers=1, log=mock.Mock()))
        self.assertEqual(out, [(1, "Arts", 0.7, 500)])


if __name__ == "__main__":
    unittest.main()
