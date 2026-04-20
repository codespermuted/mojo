"""Knowledge deduplication using TF-IDF cosine similarity."""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def is_duplicate(new_content: str, existing_contents: list[str],
                 threshold: float = 0.75) -> tuple[bool, float]:
    """Check if new knowledge duplicates existing knowledge.
    
    Returns:
        (is_dup: bool, max_similarity: float)
    """
    if not existing_contents:
        return False, 0.0

    try:
        vectorizer = TfidfVectorizer(
            analyzer="char_wb",  # Character n-grams work better for Korean+English mix
            ngram_range=(2, 4),
        )
        all_texts = [new_content] + existing_contents
        tfidf_matrix = vectorizer.fit_transform(all_texts)
        similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
        max_sim = float(max(similarities))
        return max_sim >= threshold, round(max_sim, 3)
    except ValueError:
        # Empty vocabulary (all stop words, etc.)
        return False, 0.0


def find_related(new_content: str, existing_items: list[dict],
                 top_k: int = 3,
                 min_similarity: float = 0.3) -> list[tuple[str, float]]:
    """Find related knowledge items by content similarity.

    Returns list of (id, cosine_similarity) tuples, sorted by score desc.
    The score is kept so downstream consumers can weight edges (e.g.,
    map similarity to stroke width in the graph view).
    """
    if not existing_items:
        return []

    contents = [item["content"] for item in existing_items]
    try:
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        all_texts = [new_content] + contents
        tfidf_matrix = vectorizer.fit_transform(all_texts)
        similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]

        indexed = [(float(sim), i) for i, sim in enumerate(similarities)
                   if sim >= min_similarity]
        indexed.sort(reverse=True)

        return [(existing_items[i]["id"], round(sim, 4))
                for sim, i in indexed[:top_k]]
    except ValueError:
        return []
