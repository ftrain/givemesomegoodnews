"""Which language a story is actually in.

Tagging the newsroom is not enough: plenty of outlets are bilingual, so a
Spanish article arrives under a masthead recorded as English. Function words
settle it — they are the most frequent words in any language and they barely
overlap between these four, so counting them in a headline and summary is
both accurate and free.
"""

import re

_WORD = re.compile(r"[a-záéíóúñüàèìòùâêîôûçãõ']+", re.IGNORECASE)

# Scripts that settle the question on sight, before any word counting.
SCRIPTS = [
    ("Chinese", re.compile(r"[\u4e00-\u9fff]")),
    ("Korean", re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")),
    ("Japanese", re.compile(r"[\u3040-\u30ff]")),
    ("Arabic", re.compile(r"[\u0600-\u06ff]")),
    ("Russian", re.compile(r"[\u0400-\u04ff]")),
    ("Greek", re.compile(r"[\u0370-\u03ff]")),
]

# Vietnamese is Latin-scripted, so it needs its own test: the stacked tone
# marks below are used by essentially nothing else in this catalog.
_VIETNAMESE_MARKS = re.compile(
    r"[ạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉĩọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđ]", re.IGNORECASE
)
_VIETNAMESE_WORDS = {"của", "và", "các", "người", "trong", "không", "được",
                     "một", "những", "cho", "với", "này", "đã", "là", "có"}

STOPWORDS = {
    "English": {"the", "of", "and", "to", "in", "for", "on", "with", "that", "is",
                "at", "by", "from", "as", "it", "was", "are", "this", "his", "has",
                "but", "not", "you", "have", "they", "will", "what", "about"},
    "Spanish": {"de", "la", "el", "que", "en", "los", "para", "con", "por", "una",
                "del", "las", "un", "se", "al", "su", "es", "lo", "más", "como",
                "pero", "sus", "sobre", "está", "son", "también", "años"},
    "French": {"de", "le", "la", "les", "des", "et", "en", "un", "une", "du",
                "pour", "que", "dans", "qui", "sur", "au", "aux", "est", "pas",
                "plus", "avec", "sont", "ses"},
    "Portuguese": {"de", "da", "do", "que", "em", "para", "com", "uma", "os", "as",
                   "no", "na", "dos", "por", "mais", "foi", "são", "seu"},
}
# Shared between the Romance languages, so they decide nothing on their own.
_AMBIGUOUS = STOPWORDS["Spanish"] & STOPWORDS["French"] | \
             STOPWORDS["Spanish"] & STOPWORDS["Portuguese"]

MIN_WORDS = 6
MIN_SHARE = 0.12


def detect(*texts, default="English"):
    """Best guess at the language of some headline/summary text."""
    joined = " ".join(t for t in texts if t)
    for name, pattern in SCRIPTS:
        if len(pattern.findall(joined)) >= 2:
            return name
    if len(_VIETNAMESE_MARKS.findall(joined)) >= 3 or sum(
        1 for w in joined.lower().split() if w.strip(".,:;!?") in _VIETNAMESE_WORDS
    ) >= 2:
        return "Vietnamese"

    words = []
    for text in texts:
        if text:
            words.extend(w.lower() for w in _WORD.findall(text))
    if len(words) < MIN_WORDS:
        return default
    total = len(words)
    scores = {}
    for name, stops in STOPWORDS.items():
        hits = sum(1 for w in words if w in stops)
        # Words every Romance language shares carry half weight.
        shared = sum(0.5 for w in words if w in _AMBIGUOUS and w in stops)
        scores[name] = (hits - shared) / total
    best = max(scores, key=scores.get)
    if scores[best] < MIN_SHARE:
        return default
    # English wins ties: most of this catalog is English, and a short
    # headline with one or two Romance stopwords is usually a name.
    if scores["English"] >= scores[best] - 0.01:
        return "English"
    return best
