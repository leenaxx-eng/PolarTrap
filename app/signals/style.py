"""Key S - stance-independent writing style.

Text = all of an account's posts (thread replies + history). Before features:
mask URLs/@mentions/#hashtags/numbers, replace topic words (high TF-IDF against the
corpus thread documents) and capitalised entity-like tokens with the placeholder "T".

Blocks (cosine per block, then weighted mean - "block-weighted cosine"):
  char    char 2-4-gram TF-IDF, sublinear tf, vocabulary/IDF frozen from the corpus
  func    relative frequencies of ~150 English function words      (z-scored vs corpus)
  punct   punctuation profile per character                          (z-scored)
  surface uppercase ratio, emoji rate, mean word / sentence length   (z-scored)
  idio    double spaces, lowercase sentence starts, missing apostrophes, repeated chars (z-scored)
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from .. import config

URL_RE = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|org|net|io|co|uk|social|app|gov|edu)(?:/\S*)?",
                    re.I)
MENTION_RE = re.compile(r"@[\w.-]+")
HASHTAG_RE = re.compile(r"#\w+")
NUM_RE = re.compile(r"\b\d[\d.,:%]*\b")
WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|\S")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

FUNCTION_WORDS = """a about above after again against all almost also although always am among an and
another any anyone anything are around as at be because been before being below between both but by can
could did do does doing done down during each either enough even ever every few for from further had has
have having he her here hers herself him himself his how however i if in into is it its itself just least
less let like many may me might mine more most much must my myself neither never no nobody none nor not
nothing now of off often on once one only or other others ought our ours ourselves out over own perhaps
quite rather really same shall she should since so some someone something still such than that the their
theirs them themselves then there these they this those though through thus to too toward under until up
upon us very was we were what whatever when where whether which while who whom whose why will with within
without would yet you your yours yourself yourselves""".split()
FW_INDEX = {w: i for i, w in enumerate(FUNCTION_WORDS)}
FW_SET = set(FUNCTION_WORDS)
PUNCT = list(".,!?;:'\"-()…*/") + ["’", "“", "—"]
APOS_MISSING = {"dont", "im", "cant", "wont", "isnt", "doesnt", "didnt", "ive", "youre", "thats", "theyre",
                "wasnt", "arent", "couldnt", "shouldnt", "wouldnt", "whats", "hes", "shes", "theres", "lets"}
REPEAT_RE = re.compile(r"(.)\1{2,}")

BLOCK_WEIGHTS = {"char": 0.4, "func": 0.3, "punct": 0.1, "surface": 0.1, "idio": 0.1}


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def volume(words_a: int, words_b: int) -> str:
    m = min(words_a, words_b)
    if m >= config.STYLE_FULL_WORDS:
        return "full"
    if m >= config.STYLE_REDUCED_WORDS:
        return "reduced"
    return "insufficient"


# --- masking ---------------------------------------------------------------
def basic_mask(text: str) -> str:
    text = URL_RE.sub(" U ", text)
    text = MENTION_RE.sub(" M ", text)
    text = HASHTAG_RE.sub(" H ", text)
    return NUM_RE.sub("0", text)


def mask(text: str, topic_words: set[str]) -> str:
    """basic_mask + replace topic words and capitalised non-sentence-initial tokens with T."""
    out_lines = []
    for line in basic_mask(text).split("\n"):
        parts = re.split(r"(\s+)", line)
        sent_start = True
        res = []
        for tok in parts:
            if not tok or tok.isspace():
                res.append(tok)
                continue
            m = WORD_RE.search(tok)
            if m:
                w = m.group(0)
                lw = w.lower()
                entity = (w[0].isupper() and not sent_start and lw not in FW_SET and w not in ("I", "U", "M", "H")
                          and not w.startswith("I'"))
                if lw in topic_words or entity:
                    tok = tok[:m.start()] + "T" + tok[m.end():]
            res.append(tok)
            sent_start = bool(re.search(r"[.!?]$", tok))
        out_lines.append("".join(res))
    return "\n".join(out_lines)


def topic_words(thread_texts: dict[str, str], df: dict[str, int], n_docs: int, top_k: int = 150) -> set[str]:
    """Top TF-IDF content words per thread document, IDF from the corpus thread documents."""
    words: set[str] = set()
    for text in thread_texts.values():
        c = Counter(w.lower() for w in WORD_RE.findall(basic_mask(text)))
        scored = []
        for w, tf in c.items():
            if w in FW_SET or len(w) < 3:
                continue
            idf = math.log((n_docs + 1) / (df.get(w, 0) + 1)) + 1
            scored.append(((1 + math.log(tf)) * idf, w))
        scored.sort(reverse=True)
        words.update(w for _, w in scored[:top_k])
    return words


def doc_freq(thread_texts: dict[str, str]) -> dict[str, int]:
    df: Counter = Counter()
    for text in thread_texts.values():
        df.update({w.lower() for w in WORD_RE.findall(basic_mask(text))})
    return dict(df)


# --- dense features ---------------------------------------------------------
def dense_blocks(raw_posts: list[str], masked: str) -> dict[str, np.ndarray]:
    words = [w.lower() for w in WORD_RE.findall(masked)]
    nw = max(1, len(words))
    fw = np.zeros(len(FUNCTION_WORDS))
    for w in words:
        i = FW_INDEX.get(w)
        if i is not None:
            fw[i] += 1
    fw /= nw

    raw = "\n".join(raw_posts)
    nchar = max(1, len(raw))
    punct = np.array([raw.count(p) / nchar for p in PUNCT])

    letters = [c for c in raw if c.isalpha()]
    upper = sum(c.isupper() for c in letters) / max(1, len(letters))
    emoji = len(EMOJI_RE.findall(raw)) / nw
    mean_wlen = sum(len(w) for w in words) / nw
    sents = [s for p in raw_posts for s in SENT_SPLIT_RE.split(basic_mask(p)) if WORD_RE.search(s)]
    mean_slen = sum(word_count(s) for s in sents) / max(1, len(sents))
    surface = np.array([upper, emoji, mean_wlen, math.log1p(mean_slen)])

    double_sp = raw.count("  ") / nchar
    lower_starts = sum(1 for s in sents if (m := re.search(r"[A-Za-z]", s)) and m.group(0).islower())
    lower_start_ratio = lower_starts / max(1, len(sents))
    missing_apos = sum(1 for w in words if w in APOS_MISSING) / nw
    repeats = len(REPEAT_RE.findall(raw)) / nw
    idio = np.array([double_sp, lower_start_ratio, missing_apos, repeats])
    return {"func": fw, "punct": punct, "surface": surface, "idio": idio}


def _cos(u: np.ndarray, v: np.ndarray) -> float:
    nu, nv = float(np.linalg.norm(u)), float(np.linalg.norm(v))
    if nu == 0 or nv == 0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


class StyleModel:
    """Frozen from the reference corpus: char vectorizer, dense means/stds, thread doc frequencies."""

    def __init__(self, vectorizer, dense_mean: dict, dense_std: dict, df: dict[str, int], n_docs: int):
        self.vectorizer = vectorizer
        self.dense_mean = {k: np.asarray(v) for k, v in dense_mean.items()}
        self.dense_std = {k: np.asarray(v) for k, v in dense_std.items()}
        self.df = df
        self.n_docs = n_docs

    # --- fitting ----------------------------------------------------------
    @classmethod
    def fit(cls, account_posts: dict[str, list[str]], thread_texts: dict[str, str]) -> "StyleModel":
        from sklearn.feature_extraction.text import TfidfVectorizer
        df = doc_freq(thread_texts)
        n_docs = len(thread_texts)
        tw = topic_words(thread_texts, df, n_docs)
        masked = {d: mask("\n".join(p), tw) for d, p in account_posts.items()}
        eligible = [d for d, p in account_posts.items()
                    if word_count("\n".join(p)) >= config.STYLE_REDUCED_WORDS]
        if len(eligible) < 5:
            eligible = list(account_posts)
        vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), sublinear_tf=True, min_df=2,
                              max_features=30000, lowercase=False)
        vec.fit([masked[d] for d in eligible])
        blocks = [dense_blocks(account_posts[d], masked[d]) for d in eligible]
        mean = {k: np.mean([b[k] for b in blocks], axis=0) for k in blocks[0]}
        std = {k: np.std([b[k] for b in blocks], axis=0) + 1e-9 for k in blocks[0]}
        return cls(vec, mean, std, df, n_docs)

    def save(self, path) -> None:
        import joblib
        joblib.dump({"vectorizer": self.vectorizer, "dense_mean": {k: v.tolist() for k, v in self.dense_mean.items()},
                     "dense_std": {k: v.tolist() for k, v in self.dense_std.items()},
                     "df": self.df, "n_docs": self.n_docs}, path, compress=3)

    @classmethod
    def load(cls, path) -> "StyleModel":
        import joblib
        d = joblib.load(path)
        return cls(d["vectorizer"], d["dense_mean"], d["dense_std"], d["df"], d["n_docs"])

    # --- use --------------------------------------------------------------
    def topic_words(self, thread_texts: dict[str, str]) -> set[str]:
        return topic_words(thread_texts, self.df, self.n_docs)

    def profile(self, posts: list[str], topic: set[str]) -> dict:
        """Memoised per (posts, topic set) - accounts appear in many pairs."""
        key = (hash(tuple(posts)), hash(frozenset(topic)))
        cache = self.__dict__.setdefault("_cache", {})
        if key not in cache:
            if len(cache) > 5000:
                cache.clear()
            cache[key] = self._profile(posts, topic)
        return cache[key]

    def _profile(self, posts: list[str], topic: set[str]) -> dict:
        raw ="\n".join(posts)
        masked = mask(raw, topic)
        dense = dense_blocks(posts, masked)
        z = {k: (v - self.dense_mean[k]) / self.dense_std[k] for k, v in dense.items()}
        char = self.vectorizer.transform([masked])
        return {"words": word_count(raw), "posts": len(posts), "char": char, "z": z}

    def similarity(self, pa: dict, pb: dict) -> dict:
        blocks = {"char": float((pa["char"] @ pb["char"].T).toarray()[0, 0])}
        for k in ("func", "punct", "surface", "idio"):
            blocks[k] = _cos(pa["z"][k], pb["z"][k])
        total = sum(BLOCK_WEIGHTS[k] * blocks[k] for k in BLOCK_WEIGHTS)
        return {"value": total, "blocks": {k: round(v, 4) for k, v in blocks.items()}}


def compute(model: StyleModel | None, posts_a: list[str], posts_b: list[str], topic: set[str]) -> dict:
    wa, wb = word_count("\n".join(posts_a)), word_count("\n".join(posts_b))
    vol = volume(wa, wb)
    out = {"words_a": wa, "words_b": wb, "posts_a": len(posts_a), "posts_b": len(posts_b), "volume": vol,
           "value": None, "blocks": None, "insufficient_data": vol == "insufficient", "reason": None}
    if vol == "insufficient":
        out["reason"] = (f"writing style needs at least {config.STYLE_REDUCED_WORDS} words per account "
                         f"(have {wa} and {wb})")
        return out
    if model is None:
        out.update(insufficient_data=True, reason="style model not built yet (reference corpus missing)")
        return out
    sim = model.similarity(model.profile(posts_a, topic), model.profile(posts_b, topic))
    out.update(value=sim["value"], blocks=sim["blocks"])
    return out
