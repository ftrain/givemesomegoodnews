"""Pluggable text embedders for pgvector.

The default is a deterministic local feature-hashing embedder: no model
downloads, no API keys, no GPU. It is good enough to surface "these two
stories share a topic" connections across outlets (shared distinctive
vocabulary — evictions, transit, wildfire, a person's name — lands in the
same buckets), and it can be swapped for a real embedding model later
without touching the schema, as long as the dimension stays 384 (e.g.
sentence-transformers all-MiniLM-L6-v2 is 384). To use a different
dimension: ALTER TABLE articles ALTER COLUMN embedding TYPE vector(N),
then `python3 -m givemesomegoodnews.embed --all` to re-embed.

Select with the EMBEDDER environment variable; register new embedders in
get_embedder().
"""

import hashlib
import math
import re

DIM = 384

_WORD_RE = re.compile(r"[a-z0-9']+")

_STOPWORDS = frozenset(
    """a about above after again all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during
    each few for from further had has have having he her here hers herself him
    himself his how i if in into is it its itself just me more most my myself new
    no nor not now of off on once only or other our ours ourselves out over own
    said say says she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very
    was we were what when where which while who whom why will with would you your
    yours yourself yourselves""".split()
)


def tokenize(text):
    words = [w for w in _WORD_RE.findall(text.lower()) if len(w) > 1 and w not in _STOPWORDS]
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    return words + bigrams


class HashingEmbedder:
    name = "hashing-v1"
    dim = DIM

    def embed(self, texts):
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text):
        counts = {}
        for tok in tokenize(text):
            counts[tok] = counts.get(tok, 0) + 1
        vec = [0.0] * self.dim
        for tok, c in counts.items():
            digest = hashlib.md5(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign * (1.0 + math.log(c))
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


def get_embedder(name=None):
    from . import config

    name = name or config.EMBEDDER
    if name == "hashing":
        return HashingEmbedder()
    # To plug in a model or API embedder, add a class with .name, .dim,
    # and .embed(list[str]) -> list[list[float]], and register it here.
    raise ValueError(f"unknown embedder: {name}")
