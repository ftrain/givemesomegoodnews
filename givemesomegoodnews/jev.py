"""The fourth subject layer: ask Jev about the stories the first three leave blank.

Jev is TypeSafe's System One model. It does not write anything; it is shown a
headline and summary and asked one typed question — which of the site's eleven
subjects this story belongs to, or none of them — and answers with a
probability for every option. Code keeps the answer only when one subject
clearly carries it.

Every call is one story, one question; TypeSafe bills the question's text
with every request, so the wording below is kept short. Without
TYPESAFE_API_KEY nothing here runs and the story stays untagged, as before.
"""

import os
import sys
import time

import requests

from .subjects import SUBJECTS

JEV_URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
JEV_TIMEOUT = 30
# A subject is taken only with at least this much of the probability.
MIN_P = float(os.environ.get("MIN_JEV_P", "0.6"))
# What no subject fits. The option's name is what the model reads.
NONE = "None of these"

INSTRUCTIONS = (
    "Which subject does this local news story belong to? Judge by what the story "
    "is about, not by where it was published."
)

# One line per subject, written to tell neighbours apart: News from Politics,
# Housing from Business, Food from Business, and the things that are not
# stories at all from all of them.
CRITERIA = {
    "News": "A reported local event or incident — crime, accident, fire, weather, "
            "public safety, a community happening — that no subject below covers",
    "Politics": "Government and politics: elections, candidates, legislatures, councils, "
                "courts and criminal justice, immigration, police policy, public officials",
    "Education": "Schools, students, teachers, school boards, colleges and universities",
    "Health": "Health care, hospitals, public health, mental health, disease, medicine",
    "Environment": "Environment, climate, energy, water, wildlife, conservation, pollution, "
                   "wildfires and floods as natural events",
    "Housing": "Housing, homelessness, rent, eviction, real estate, zoning and development",
    "Business": "Business, the economy, jobs and labor, technology, transportation and "
                "transit, local companies opening or closing",
    "Arts": "Arts and culture: music, film, theater, books, museums, media, festivals",
    "Sports": "Sports and athletics at any level, including school and college teams",
    "Food": "Food and drink: restaurants, dining, recipes, markets, breweries",
    "Opinion": "An editorial, column, letter to the editor or commentary written to persuade",
    NONE: "Not a news story: an obituary or death notice, an event calendar, a "
          "horoscope, a puzzle, a classified or advertisement, site housekeeping",
}
assert set(CRITERIA) == set(SUBJECTS) | {NONE}


def request(title, summary):
    return {
        "model": JEV_MODEL,
        "state": {"headline": title, "summary": summary or ""},
        "questions": {"subject": {"type": "choice", "instructions": INSTRUCTIONS,
                                  "criteria": CRITERIA}},
    }


def ask(title, summary, api_key, session=None):
    """Jev's answer for one story: (subject or None, probability, input tokens).

    None for the subject means no subject carried MIN_P or the model chose
    none of them; the tokens are still spent and reported. Raises only when
    the service cannot be reached after retrying — a 429 or 529 is backed off
    and retried, anything else is an error in the request and is raised.
    """
    http = session or requests
    headers = {"Authorization": f"Bearer {api_key}"}
    delay = 1.0
    for attempt in range(5):
        r = http.post(JEV_URL, json=request(title, summary), headers=headers, timeout=JEV_TIMEOUT)
        if r.status_code in (429, 529) and attempt < 4:
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        body = r.json()
        answer = body["answers"]["subject"]
        tokens = int(body.get("usage", {}).get("input_tokens") or 0)
        probs = answer.get("probabilities") or {}
        subject, p = answer.get("choice"), float(probs.get(answer.get("choice"), 0.0))
        if subject not in SUBJECTS or p < MIN_P:
            return None, p, tokens
        return subject, p, tokens
    raise RuntimeError("jev: rate limited after retries")


def judge(rows, api_key, workers=8, log=None):
    """Ask about every (id, title, summary) row, in parallel.

    Yields (id, subject or None, probability, tokens) as answers arrive. A
    story whose request fails is skipped and reported, not retried here: the
    next run asks again, since it is still unjudged.
    """
    from concurrent.futures import ThreadPoolExecutor

    session = requests.Session()

    def one(row):
        article_id, title, summary = row
        try:
            subject, p, tokens = ask(title, summary, api_key, session)
            return article_id, subject, p, tokens, None
        except Exception as e:  # noqa: BLE001 — reported, never fatal to the run
            return article_id, None, 0.0, 0, f"{type(e).__name__}: {str(e)[:120]}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for article_id, subject, p, tokens, err in pool.map(one, rows):
            if err:
                print(f"jev: article {article_id}: {err}", file=log or sys.stderr)
                continue
            yield article_id, subject, p, tokens
