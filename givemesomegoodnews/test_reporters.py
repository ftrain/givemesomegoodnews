"""Tests for byline resolution: python3 -m unittest givemesomegoodnews.test_reporters

The parser tests need nothing but the stdlib. The tests that exercise the
tables want a database and skip themselves when there isn't one, so this runs
either way.
"""

import unittest

from .reporters import credit, normalize, parse_byline, slugify

try:
    from .db import connect
except ImportError:  # psycopg missing; the parser tests still run
    connect = None


def names(author):
    return [b.name for b in parse_byline(author)]


class ParseBylineTest(unittest.TestCase):
    def test_plain_name(self):
        self.assertEqual(names("Jane Doe"), ["Jane Doe"])

    def test_strips_leading_by(self):
        self.assertEqual(names("By Jane Doe"), ["Jane Doe"])
        self.assertEqual(names("Story by Jane Doe"), ["Jane Doe"])
        self.assertEqual(names("Reported by Jane Doe"), ["Jane Doe"])

    def test_leading_by_does_not_eat_a_name_starting_with_by(self):
        self.assertEqual(names("Byron Smith"), ["Byron Smith"])

    def test_strips_trailing_outlet_credit(self):
        self.assertEqual(names("By Emily Wunderlich, Times staff"),
                         ["Emily Wunderlich"])

    def test_casing_variants_are_one_identity(self):
        a = parse_byline("By Emily Wunderlich, Times staff")[0]
        b = parse_byline("emily wunderlich")[0]
        self.assertEqual(a.key, b.key)
        self.assertEqual(b.name, "Emily Wunderlich")

    def test_two_people(self):
        self.assertEqual(names("Jane Doe and John Roe"), ["Jane Doe", "John Roe"])

    def test_other_separators(self):
        for author in ("Jane Doe, John Roe", "Jane Doe & John Roe",
                       "Jane Doe / John Roe", "Jane Doe; John Roe",
                       "Jane Doe y John Roe"):
            self.assertEqual(names(author), ["Jane Doe", "John Roe"], author)

    def test_three_people(self):
        self.assertEqual(names("By Jane Doe, John Roe and Ann Poe"),
                         ["Jane Doe", "John Roe", "Ann Poe"])

    def test_oxford_comma_before_the_conjunction(self):
        self.assertEqual(names("Jane Doe, John Roe, and Ann Poe"),
                         ["Jane Doe", "John Roe", "Ann Poe"])

    def test_spaced_dash_separates_a_trailing_wire_credit(self):
        self.assertEqual(names("By LINDSAY WHITEHURST and NICHOLAS RICCARDI - Associated Press"),
                         ["Lindsay Whitehurst", "Nicholas Riccardi"])

    def test_hyphenated_surname_survives(self):
        self.assertEqual(names("Fred Clasen-Kelly"), ["Fred Clasen-Kelly"])

    def test_quoted_nickname_is_dropped(self):
        self.assertEqual(names('Ronald "DC" Reynolds'), ["Ronald Reynolds"])

    def test_a_wholly_quoted_byline_is_not_a_nickname(self):
        self.assertEqual(names('"Erin Nolan"'), ["Erin Nolan"])

    def test_contact_address_after_a_name(self):
        self.assertEqual(names("By Kendra Gilchrist kgilchrist@example.com"),
                         ["Kendra Gilchrist"])

    def test_the_same_person_twice_is_credited_once(self):
        self.assertEqual(names("Jane Doe and Jane Doe"), ["Jane Doe"])

    def test_byline_text_is_the_original_span(self):
        found = parse_byline("By Emily Wunderlich, Times staff")
        self.assertEqual(found[0].text, "Emily Wunderlich")

    def test_middle_initial_is_not_a_spanish_conjunction(self):
        self.assertEqual(names("Jane Y. Doe"), ["Jane Y. Doe"])

    def test_initials(self):
        self.assertEqual(names("J. K. Rowling"), ["J. K. Rowling"])

    def test_parenthetical_is_dropped(self):
        self.assertEqual(names("Jane Doe (Times)"), ["Jane Doe"])

    def test_email_alongside_a_name(self):
        self.assertEqual(names("Jane Doe, jdoe@times.com"), ["Jane Doe"])

    def test_mixed_case_is_preserved(self):
        self.assertEqual(names("Ronan McDonald"), ["Ronan McDonald"])
        self.assertEqual(names("Siobhan O'Brien"), ["Siobhan O'Brien"])

    def test_shouted_byline_is_calmed_down(self):
        self.assertEqual(names("JANE DOE"), ["Jane Doe"])

    def test_particles_stay_lowercase(self):
        self.assertEqual(names("piet van der berg"), ["Piet van der Berg"])

    def test_accented_name_keeps_its_accents_on_the_page(self):
        self.assertEqual(names("José Pérez"), ["José Pérez"])

    def test_accent_variants_are_one_identity(self):
        self.assertEqual(normalize("José Pérez"), normalize("Jose Perez"))


class RejectionTest(unittest.TestCase):
    def test_the_acceptance_list(self):
        for author in ("By the Editorial Board", "Associated Press",
                       "Staff report", "Times Staff", "newsroom@example.org"):
            self.assertEqual(parse_byline(author), [], author)

    def test_more_desks_wires_and_titles(self):
        for author in ("Newsroom Staff", "The Conversation", "Reuters",
                       "Editorial Board", "Staff Writer", "Special to the Times",
                       "AP", "Metro Desk", "Guest Columnist", "Anonymous",
                       "Sports Desk", "Contributed Report", "Press Association",
                       "The Associated Press", "News Service",
                       "National Congress of Black Women", "Institute for Justice",
                       "Tabor City Promotions Director", "Managing Editor"):
            self.assertEqual(parse_byline(author), [], author)

    def test_empty_byline(self):
        self.assertEqual(parse_byline(None), [])
        self.assertEqual(parse_byline(""), [])
        self.assertEqual(parse_byline("   "), [])

    def test_single_word_is_not_a_person(self):
        self.assertEqual(parse_byline("Madonna"), [])

    def test_digits_are_not_a_person(self):
        self.assertEqual(parse_byline("Jane Doe 2"), [])

    def test_a_sentence_is_not_a_person(self):
        self.assertEqual(parse_byline("This story was produced in partnership"), [])

    def test_a_url_is_not_a_person(self):
        self.assertEqual(parse_byline("https://example.org/team"), [])
        self.assertEqual(parse_byline("www.example.org"), [])
        self.assertEqual(parse_byline("example.org"), [])

    def test_initials_with_no_surname_are_not_a_person(self):
        self.assertEqual(parse_byline("J. K."), [])

    def test_a_profile_url_after_a_name_is_cut_off(self):
        self.assertEqual(names("Jane Doe https://times.example/staff/jane"),
                         ["Jane Doe"])


class SlugTest(unittest.TestCase):
    def test_url_safe(self):
        self.assertEqual(slugify("Emily Wunderlich"), "emily-wunderlich")
        self.assertEqual(slugify("José Pérez"), "jose-perez")
        self.assertEqual(slugify("Siobhan O'Brien"), "siobhan-obrien")
        self.assertEqual(slugify("J. K. Rowling"), "j-k-rowling")

    def test_no_leading_or_trailing_separator(self):
        for name in ("Jane Doe", "José Pérez", "J. K. Rowling", "Piet van der Berg",
                     # long enough that the 60-char cut lands on a separator
                     "Bartholomew Fitzwilliam Montgomery Wodehouse Featherstone A Xyz"):
            slug = slugify(name)
            self.assertRegex(slug, r"^[a-z0-9]+(?:-[a-z0-9]+)*$", name)


def _has_db():
    if connect is None:
        return False
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM reporters LIMIT 0")
        return True
    except Exception:
        return False


@unittest.skipUnless(_has_db(), "no database")
class CreditTest(unittest.TestCase):
    """The tables, exercised on throwaway rows that are rolled back."""

    def setUp(self):
        self.conn = connect()
        self.conn.autocommit = False
        self.cur = self.conn.cursor()
        self.orgs = []
        for slug in ("test-alpha-news", "test-beta-news"):
            self.cur.execute(
                "INSERT INTO orgs (slug, name, url) VALUES (%s, %s, %s) RETURNING id",
                (slug, slug, f"https://{slug}.example"),
            )
            self.orgs.append(self.cur.fetchone()[0])

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def article(self, org_id, author, n=[0]):
        n[0] += 1
        self.cur.execute(
            "INSERT INTO articles (org_id, url, title, author) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (org_id, f"https://example.test/story-{n[0]}-{id(self)}", "A story", author),
        )
        return self.cur.fetchone()[0]

    def reporters_at(self, org_id):
        self.cur.execute(
            "SELECT name, slug FROM reporters WHERE org_id = %s ORDER BY name",
            (org_id,),
        )
        return self.cur.fetchall()

    def test_two_spellings_one_identity(self):
        org = self.orgs[0]
        credit(self.cur, self.article(org, "By Emily Wunderlich, Times staff"),
               org, "By Emily Wunderlich, Times staff")
        credit(self.cur, self.article(org, "emily wunderlich"),
               org, "emily wunderlich")
        self.assertEqual(self.reporters_at(org),
                         [("Emily Wunderlich", "emily-wunderlich")])

    def test_multi_byline_credits_both(self):
        org = self.orgs[0]
        article_id = self.article(org, "Jane Doe and John Roe")
        credit(self.cur, article_id, org, "Jane Doe and John Roe")
        self.assertEqual(len(self.reporters_at(org)), 2)
        self.cur.execute(
            "SELECT count(*) FROM article_reporters WHERE article_id = %s", (article_id,)
        )
        self.assertEqual(self.cur.fetchone()[0], 2)

    def test_rejected_bylines_create_nothing(self):
        org = self.orgs[0]
        for author in ("By the Editorial Board", "Associated Press", "Staff report",
                       "Times Staff", "newsroom@example.org"):
            credit(self.cur, self.article(org, author), org, author)
        self.assertEqual(self.reporters_at(org), [])

    def test_same_name_at_two_orgs_is_two_identities(self):
        for org in self.orgs:
            credit(self.cur, self.article(org, "Jane Doe"), org, "Jane Doe")
        self.cur.execute(
            "SELECT count(*) FROM reporters WHERE org_id = ANY(%s) AND match_key = 'jane doe'",
            (self.orgs,),
        )
        self.assertEqual(self.cur.fetchone()[0], 2)

    def test_rerunning_adds_nothing(self):
        org = self.orgs[0]
        article_id = self.article(org, "Jane Doe and John Roe")
        credit(self.cur, article_id, org, "Jane Doe and John Roe")
        before = self.counts()
        credit(self.cur, article_id, org, "Jane Doe and John Roe")
        self.assertEqual(self.counts(), before)

    def test_slug_is_unique_within_a_newsroom(self):
        org = self.orgs[0]
        # Two distinct identities ("obrien" vs "o brien") that slugify alike.
        for author in ("Siobhan O'Brien", "Siobhan O Brien"):
            credit(self.cur, self.article(org, author), org, author)
        slugs = [s for _, s in self.reporters_at(org)]
        self.assertEqual(len(slugs), 2)
        self.assertEqual(len(set(slugs)), 2)

    def counts(self):
        self.cur.execute("SELECT count(*) FROM reporters")
        reporters = self.cur.fetchone()[0]
        self.cur.execute("SELECT count(*) FROM article_reporters")
        return reporters, self.cur.fetchone()[0]


if __name__ == "__main__":
    unittest.main()
