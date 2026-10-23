"""Семантичний пошук книг за embeddings.

Вектори книг (Book.embedding) тримаються в пам'яті однією матрицею numpy,
схожість рахується як скалярний добуток нормованих векторів (косинусна близькість).
"""
import logging

import numpy as np
from django.db.models import Q

from .models import Book
from .services import AIUnavailable, embed_texts

logger = logging.getLogger(__name__)

_index = {'count': -1, 'ids': None, 'matrix': None}

TEXT_FIELDS = ('title', 'author', 'description', 'plot_keywords')


def word_condition(word: str, fields=TEXT_FIELDS) -> Q:
    """Умова «слово трапляється в будь-якому з полів» без урахування регістру.
    SQLite ігнорує регістр лише для латиниці, тому для кирилиці перебираємо
    варіанти написання: «дюна», «Дюна», «ДЮНА»."""
    condition = Q()
    for variant in {word, word.lower(), word.capitalize(), word.upper()}:
        for field in fields:
            condition |= Q(**{f'{field}__contains': variant})
    return condition


def book_text(book) -> str:
    """Текст книги, з якого рахується embedding."""
    parts = [book.title, book.author, book.genre, book.plot_keywords, (book.description or '')[:1500]]
    return '. '.join(p for p in parts if p)


def normalize(vector) -> np.ndarray:
    """Вектор довжини 1 (тоді скалярний добуток = косинусна близькість)."""
    v = np.asarray(vector, dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)


def to_bytes(vector) -> bytes:
    """Нормований вектор у байти для збереження в БД (float32 - 4 байти на число)."""
    return normalize(vector).tobytes()


def _get_index():
    """Матриця всіх векторів каталогу (кешується в пам'яті процесу)."""
    count = Book.objects.exclude(embedding=None).count()
    if count != _index['count']:
        rows = Book.objects.exclude(embedding=None).values_list('id', 'embedding')
        ids, vectors = [], []
        for book_id, blob in rows:
            ids.append(book_id)
            vectors.append(np.frombuffer(bytes(blob), dtype=np.float32))
        _index.update(count=count, ids=np.array(ids),
                      matrix=np.vstack(vectors) if vectors else None)
    return _index


def _top_books(query_vector, k, exclude_id=None):
    index = _get_index()
    if index['matrix'] is None:
        return []
    scores = index['matrix'] @ query_vector  # косинусна близькість з кожною книгою
    order = np.argsort(-scores)[:k + 1]  # +1 на випадок, якщо серед них сама книга
    ids = [int(index['ids'][i]) for i in order if int(index['ids'][i]) != exclude_id][:k]
    books = Book.objects.in_bulk(ids)
    return [books[i] for i in ids if i in books]


def keyword_search(query: str, k: int, fallback_latest: bool = True):
    """Пошук за словами запиту в назві, авторі, описі та ключових словах.
    Книги, де збіглося більше слів, ідуть першими."""
    words = list(dict.fromkeys(w.lower().strip('.,!?«»"()') for w in query.split()))
    words = [w for w in words if len(w) > 3][:10]
    if not words:
        return list(Book.objects.order_by('-created_at')[:k]) if fallback_latest else []
    condition = Q()
    for w in words:
        condition |= word_condition(w)
    candidates = Book.objects.filter(condition)[:200]

    def matched_words(book):
        text = f'{book.title} {book.author} {book.plot_keywords} {book.description}'.lower()
        return sum(w in text for w in words)

    books = sorted(candidates, key=matched_words, reverse=True)[:k]
    if not books and fallback_latest:
        books = list(Book.objects.order_by('-created_at')[:k])
    return books


def search_books(query: str, k: int = 20):
    """Гібридний пошук: найближчі за змістом книги + кілька книг за словами запиту
    (щоб знаходились книги без вектора і точні назви). Без векторів - лише за словами."""
    if _get_index()['matrix'] is None:
        return keyword_search(query, k)
    try:
        query_vector = normalize(embed_texts([query], 'RETRIEVAL_QUERY')[0])
    except AIUnavailable as e:
        logger.warning(e)
        return keyword_search(query, k)

    semantic = _top_books(query_vector, k)
    extra_k = max(3, k // 4)
    by_words = keyword_search(query, extra_k, fallback_latest=False)
    seen = {b.id for b in semantic}
    return semantic + [b for b in by_words if b.id not in seen]


def similar_books(book, k: int = 6):
    """Схожі за змістом книги - без запиту до API, лише за збереженими векторами."""
    if not book.embedding:
        return []
    return _top_books(np.frombuffer(bytes(book.embedding), dtype=np.float32), k, exclude_id=book.id)


def index_book(book):
    """Обчислює вектор для однієї нової книги (щоб вона одразу знаходилась пошуком)."""
    try:
        book.embedding = to_bytes(embed_texts([book_text(book)], 'RETRIEVAL_DOCUMENT')[0])
        book.save(update_fields=['embedding'])
        _index['count'] = -1  # вектор змінився - матрицю треба перебудувати
    except AIUnavailable as e:
        logger.warning(e)  # не критично: вектор можна дорахувати командою build_embeddings


def categorized_neighbors(book, k: int = 7):
    """k найближчих за змістом книг, у яких є категорія (для автокатегоризації)."""
    if not book.embedding:
        return []
    candidates = _top_books(np.frombuffer(bytes(book.embedding), dtype=np.float32), 50, exclude_id=book.id)
    return [b for b in candidates if b.category_id][:k]
