"""Робота з Gemini (генерація, embeddings) та Open Library."""
import logging
from functools import lru_cache
from typing import Optional

import httpx
from django.conf import settings
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from .models import Category

logger = logging.getLogger(__name__)


class AIUnavailable(Exception):
    """Gemini недоступний: не вказано ключ або сталася помилка під час запиту."""


# Схеми відповідей Gemini

class IdentifiedBook(BaseModel):
    title: str
    author: str
    originalTitle: Optional[str]
    year: Optional[int]
    confidence: int
    inCatalog: bool
    localBookId: Optional[str]
    summary: str
    matchExplanation: str
    matchingPlotPoints: list[str]
    openLibraryQuery: Optional[str]


class AlternativeCandidate(BaseModel):
    title: str
    author: str
    inCatalog: bool
    localBookId: Optional[str]
    reason: str


class DetectiveResult(BaseModel):
    identifiedBook: IdentifiedBook
    alternativeCandidates: list[AlternativeCandidate]
    aiCommentary: str


class CatalogRecommendation(BaseModel):
    bookId: Optional[str]
    title: str
    author: str
    whyRecommended: str
    matchScore: int


class ExternalRecommendation(BaseModel):
    title: str
    author: str
    year: Optional[int]
    genre: str
    description: str
    whyRecommended: str
    searchQuery: str


class Recommendations(BaseModel):
    explanation: str
    catalogRecommendations: list[CatalogRecommendation]
    externalRecommendations: list[ExternalRecommendation]


class BookEnrichment(BaseModel):
    known: bool
    description: str
    genre: str
    category_slug: str
    plot_keywords: str


# Базова робота з Gemini

# зрозумілі повідомлення для типових помилок API замість сирої відповіді Google
GEMINI_ERRORS = {
    429: 'Вичерпано ліміт запитів до Gemini, спробуйте пізніше.',
    503: 'Gemini зараз перевантажений, спробуйте за хвилину.',
}


@lru_cache(maxsize=1)
def _client(api_key: str) -> genai.Client:
    """Клієнт створюється один раз і далі перевикористовується."""
    return genai.Client(api_key=api_key)


def ask_gemini(contents, *, system: str = None, schema: type[BaseModel] = None,
               temperature: float = None, max_tokens: int = None):
    """Запит до Gemini. Зі schema повертає dict, без неї - текст."""
    if not settings.GEMINI_API_KEY:
        raise AIUnavailable('Не налаштовано GEMINI_API_KEY у файлі .env')

    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type='application/json' if schema else None,
        response_schema=schema,
    )
    # якщо основна модель перевантажена або її ліміт вичерпано - пробуємо запасну
    models = [m for m in (settings.GEMINI_MODEL, settings.GEMINI_FALLBACK_MODEL) if m]
    for model in models:
        try:
            response = _client(settings.GEMINI_API_KEY).models.generate_content(
                model=model, contents=contents, config=config)
            if schema is None:
                return response.text or ''
            return schema.model_validate_json(response.text).model_dump()
        except errors.APIError as e:
            logger.warning('Gemini %s: %s %s', model, e.code, e.message)
            if e.code in GEMINI_ERRORS and model != models[-1]:
                continue
            raise AIUnavailable(GEMINI_ERRORS.get(e.code, f'Помилка звернення до Gemini: {e.message}')) from e
        except Exception as e:
            raise AIUnavailable(f'Помилка звернення до Gemini: {e}') from e


def embed_texts(texts: list[str], task_type: str) -> list[list[float]]:
    """Вектори для текстів. task_type: 'RETRIEVAL_DOCUMENT' для книг, 'RETRIEVAL_QUERY' для запиту."""
    if not settings.GEMINI_API_KEY:
        raise AIUnavailable('Не налаштовано GEMINI_API_KEY у файлі .env')
    try:
        response = _client(settings.GEMINI_API_KEY).models.embed_content(
            model=settings.GEMINI_EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=settings.EMBEDDING_DIM),
        )
        return [e.values for e in response.embeddings]
    except Exception as e:
        raise AIUnavailable(f'Помилка обчислення embeddings: {e}') from e


def catalog_for_prompt(books, with_summary: bool = False) -> str:
    """Список книг для промпту: id, назва, автор, жанр, ключові слова (і початок опису)."""
    lines = []
    for b in books:
        line = f'id={b.id} | «{b.title}» - {b.author} | {b.genre}'
        if b.plot_keywords:
            line += f' | ключові слова: {b.plot_keywords}'
        if with_summary:
            line += ' | ' + ' '.join(b.description[:250].split())  # без переносів рядків
        lines.append(line)
    return '\n'.join(lines) or '(каталог порожній)'


# Функції ШІ-асистента

def identify_book_by_plot(plot_description: str, catalog_text: str) -> dict:
    """Детектив сюжету: визначає книгу за описом - спочатку в каталозі, потім у світовій літературі."""
    system = """Ти - літературний детектив і бібліограф. За описом сюжету від користувача визнач, про яку книгу йдеться.
1. Спершу перевір книги ЛОКАЛЬНОГО КАТАЛОГУ. Якщо збігається - inCatalog: true і точний localBookId (число з поля id).
2. Якщо в каталозі немає - визнач книгу зі світової літератури: inCatalog: false, localBookId: null,
   openLibraryQuery - оригінальна назва та автор латиницею (наприклад "Fahrenheit 451 Ray Bradbury").
3. Називай лише реальні книги. Якщо не впевнений - знизь confidence.
Пояснення (matchExplanation, matchingPlotPoints, aiCommentary) - живою українською мовою."""
    user = f'ЛОКАЛЬНИЙ КАТАЛОГ:\n{catalog_text}\n\nОпис сюжету від користувача:\n"{plot_description}"'
    return ask_gemini(user, system=system, schema=DetectiveResult)


def get_ai_recommendations(prompt: str, mood: str, genre: str, catalog_text: str) -> dict:
    """Рекомендації за запитом, настроєм і жанром: з каталогу та зі світової літератури."""
    system = f"""Ти - літературний куратор. Підбери книги під запит читача.
ЛОКАЛЬНИЙ КАТАЛОГ:
{catalog_text}

1. catalogRecommendations: 1-3 книги з каталогу (bookId - число з поля id, matchScore 60-98, whyRecommended).
   Якщо нічого не підходить - порожній список.
2. externalRecommendations: 1-2 реальні книги, яких немає в каталозі. searchQuery - оригінальна назва
   та автор латиницею (наприклад "Dune Frank Herbert"); за ним система перевіряє, що книга існує.
3. explanation - коротке вступне слово українською."""
    user = (f'Запит: "{prompt or "Порадь книги під мій настрій"}"\n'
            f'Настрій: {mood or "будь-який"}\nЖанр: {genre or "будь-який"}')
    return ask_gemini(user, system=system, schema=Recommendations)


def send_chat_message(messages: list, current_book: dict = None, catalog_text: str = '') -> str:
    """Діалог з літературним асистентом (останні 8 повідомлень історії).
    catalog_text - книги каталогу, близькі до запиту: асистент радить насамперед їх."""
    system = ('Ти - доброзичливий літературний асистент «БукЛіга». Допомагаєш знаходити книги, '
              'розбиратися в сюжеті й персонажах. Відповідай українською, стисло (2-3 абзаци), '
              'якщо не просять детальніше. Не вигадуй книг, авторів і цитат.')
    if current_book:
        system += f'\nКонтекст: користувач дивиться книгу «{current_book.get("title")}» ({current_book.get("author")}).'
    if catalog_text:
        system += ('\nКниги з каталогу сайту, які можуть стосуватися запиту (рекомендуючи, '
                   f'віддавай їм перевагу і називай точно):\n{catalog_text}')
    contents = [
        {'role': 'model' if m.get('role') == 'assistant' else 'user', 'parts': [{'text': m.get('content', '')}]}
        for m in messages[-8:]
    ]
    # ліміт з запасом: у Gemini 3 у max_output_tokens входять і «роздуми» моделі,
    # тому з малим лімітом відповідь обрізається на півслові
    return ask_gemini(contents, system=system, temperature=0.7, max_tokens=4096).strip()


def enrich_book_with_ai(title: str, author: str, raw_subjects: str = '', source_description: str = '') -> Optional[dict]:
    """Опис (або переклад справжнього опису), жанр, категорія і ключові слова від ШІ.
    Рік, сторінки та цитати ШІ не вигадує. None, якщо ШІ недоступний."""
    categories = ', '.join(f'{c.slug} ({c.name})' for c in Category.objects.all())

    if source_description:
        task = (f'Ось справжній опис книги:\n"""{source_description[:3000]}"""\n'
                'description: стисло перекажи його українською (3-5 речень), лише за фактами з опису. known: true.')
    else:
        task = ('known: true лише якщо ти впевнено знаєш сюжет саме цієї книги. '
                'description: якщо known - анотація українською (3-5 речень), інакше порожній рядок. НЕ вигадуй сюжет.')
    prompt = f"""Книга: "{title}", автор: "{author}". Мітки: "{raw_subjects}".
{task}
genre: точний літературний жанр українською.
category_slug: одна з категорій каталогу: {categories}.
plot_keywords: 4-7 ключових слів сюжету через кому українською."""
    try:
        data = ask_gemini(prompt, schema=BookEnrichment)
    except AIUnavailable as e:
        logger.warning(e)
        return None
    if not data['known']:
        data['description'] = ''
    return data


# Open Library (ключ API не потрібен)

OPEN_LIBRARY_HEADERS = {'User-Agent': 'BookLigaDjango/1.0 (education)'}


def _open_library_get(path: str, params: dict = None) -> Optional[dict]:
    try:
        with httpx.Client(timeout=10.0, headers=OPEN_LIBRARY_HEADERS) as client:
            res = client.get(f'https://openlibrary.org{path}', params=params)
        return res.json() if res.status_code == 200 else None
    except Exception as e:
        logger.warning('Open Library error (%s): %s', path, e)
        return None


def search_open_library_api(query: str, limit: int = 12) -> list:
    """Пошук книг в Open Library."""
    data = _open_library_get('/search.json', {'q': query, 'limit': limit})
    results = []
    for doc in (data or {}).get('docs', []):
        isbn_list = doc.get('isbn') or []
        if doc.get('cover_i'):
            cover_url = f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg"
        elif isbn_list:
            cover_url = f'https://covers.openlibrary.org/b/isbn/{isbn_list[0]}-M.jpg'
        else:
            cover_url = None
        results.append({
            'key': doc.get('key'),
            'title': doc.get('title', 'Без назви'),
            'author': ', '.join((doc.get('author_name') or ['Невідомий автор'])[:2]),
            'year': doc.get('first_publish_year'),
            'pages': doc.get('number_of_pages_median'),
            'cover_url': cover_url,
            'subjects': (doc.get('subject') or [])[:4],
            'isbn': isbn_list[0] if isbn_list else None,
        })
    return results


def fetch_open_library_work(work_key: str) -> dict:
    """Справжній опис і теми твору (опис зберігається в записі «твору», а не в результатах пошуку)."""
    if not work_key or not work_key.startswith('/works/'):
        return {}
    data = _open_library_get(f'{work_key}.json') or {}
    description = data.get('description')
    if isinstance(description, dict):  # буває рядком або об'єктом {"value": "..."}
        description = description.get('value')
    return {'description': (description or '').strip(), 'subjects': (data.get('subjects') or [])[:8]}


def verify_book_in_open_library(query: str) -> Optional[dict]:
    """Перевіряє, що книга, яку назвав ШІ, існує: повертає перший збіг в Open Library або None."""
    results = search_open_library_api(query, limit=3) if query else []
    return results[0] if results else None
