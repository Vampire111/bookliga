import json

from django.core.paginator import Paginator
from django.db.models import Count, F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import AiLog, Book, Category, UserBookShelf
from .semantic import index_book, search_books, similar_books, word_condition
from .services import (AIUnavailable, catalog_for_prompt, enrich_book_with_ai, fetch_open_library_work,
                       get_ai_recommendations, identify_book_by_plot, search_open_library_api,
                       send_chat_message, verify_book_in_open_library)


# Допоміжні функції

def parse_int(value):
    """Ціле число з рядка форми або None, якщо значення порожнє чи некоректне."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def log_ai(action_type, prompt_text, result):
    """Записує запит до ШІ та відповідь у журнал (видно в адмінці)."""
    AiLog.objects.create(action_type=action_type, prompt_text=prompt_text,
                         ai_response=json.dumps(result, ensure_ascii=False, default=str))


def find_catalog_book(book_id):
    """Книга каталогу за id, який повернув ШІ (рядок), або None."""
    book_id = parse_int(book_id)
    return Book.objects.filter(id=book_id).first() if book_id else None


def verify_detective_result(result):
    """Перевіряє відповідь детективу, щоб не показувати вигадані книги.
    1) Якщо ШІ каже «є в каталозі», перевіряємо, що такий id справді існує.
    2) Якщо книги в каталозі немає, шукаємо її в Open Library (поле verified)."""
    book = result['identifiedBook']
    local = find_catalog_book(book['localBookId']) if book['inCatalog'] else None
    book['inCatalog'] = bool(local)
    book['db_id'] = local.id if local else None
    if local:
        book['verified'] = True
    else:
        match = verify_book_in_open_library(book['openLibraryQuery'] or f"{book['title']} {book['author']}")
        book['verified'] = bool(match)
    for alt in result['alternativeCandidates']:
        if not alt['inCatalog']:
            alt['verified'] = bool(verify_book_in_open_library(f"{alt['title']} {alt['author']}"))
    return result


def verify_external_recommendations(result):
    """Позначає зовнішні рекомендації, знайдені в Open Library (verified=True)."""
    for ext in result['externalRecommendations']:
        match = verify_book_in_open_library(ext['searchQuery'] or f"{ext['title']} {ext['author']}")
        ext['verified'] = bool(match)
        if match and not ext['year']:
            ext['year'] = match.get('year')
    return result


def build_description(title, author, subjects, work_key=None):
    """Опис книги: спочатку з Open Library (ШІ перекладає), потім від ШІ, якщо він знає книгу.
    Повертає (дані від ШІ, опис, джерело опису)."""
    work = fetch_open_library_work(work_key) if work_key else {}
    real_description = work.get('description', '')
    if not subjects and work.get('subjects'):
        subjects = ', '.join(work['subjects'])

    ai_data = enrich_book_with_ai(title, author, subjects, source_description=real_description) or {}
    description = (ai_data.get('description') or '').strip()

    if real_description:
        # якщо ШІ недоступний, лишаємо оригінальний опис англійською
        return ai_data, description or real_description, 'open_library'
    if description:
        return ai_data, description, 'ai'
    return ai_data, 'Опис відсутній.', 'none'


def apply_ai_classification(book, ai_data):
    """Жанр, категорія та ключові слова від ШІ (якщо він відповів)."""
    book.genre = ai_data.get('genre') or book.genre
    book.plot_keywords = ai_data.get('plot_keywords') or book.plot_keywords
    category = Category.objects.filter(slug=ai_data.get('category_slug')).first()
    if category:
        book.category = category


# Каталог і сторінка книги

BOOKS_PER_PAGE = 24


def catalog_view(request):
    """Головна сторінка каталогу з пошуком, категоріями, фільтрами полиці та сортуванням."""
    query = request.GET.get('q', '').strip()
    category_slug = request.GET.get('category', 'all')
    genre = request.GET.get('genre', 'all')
    status_filter = request.GET.get('status', 'all')
    sort_by = request.GET.get('sort', 'year_desc')

    books = Book.objects.select_related('category')  # категорію беремо одним запитом разом із книгами

    # кожне слово запиту має десь трапитись (у будь-якому регістрі),
    # тому «стівен кінг» знаходить «Стівен Кінг», а порядок слів неважливий
    for word in query.split():
        books = books.filter(word_condition(word))

    if category_slug != 'all':
        books = books.filter(category__slug=category_slug)

    if genre != 'all':
        books = books.filter(genre=genre)

    # статуси книг на полиці: {book_id: status}
    user_shelves = {}
    if request.user.is_authenticated:
        entries = UserBookShelf.objects.filter(user=request.user)
        user_shelves = {e.book_id: e.status for e in entries}
    else:
        sess_shelves = request.session.get('user_shelves', {})
        user_shelves = {int(k): v for k, v in sess_shelves.items() if str(k).isdigit()}

    if status_filter != 'all':
        matching_ids = [bid for bid, st in user_shelves.items() if st == status_filter]
        books = books.filter(id__in=matching_ids)

    # книги без року в кінці списку
    if sort_by == 'year_desc':
        books = books.order_by(F('year').desc(nulls_last=True), 'title')
    elif sort_by == 'year_asc':
        books = books.order_by(F('year').asc(nulls_last=True), 'title')
    elif sort_by == 'title':
        books = books.order_by('title')

    paginator = Paginator(books, BOOKS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get('page'))

    # фільтри для посилань пагінації
    params = request.GET.copy()
    params.pop('page', None)
    filters_query = params.urlencode()

    # категорії з книгами, спочатку найбільші
    categories = (Category.objects.annotate(books_total=Count('books'))
                  .filter(books_total__gt=0).order_by('-books_total', 'name'))
    # без order_by('genre') distinct враховує ordering моделі і жанри дублюються
    all_genres = Book.objects.exclude(genre='').order_by('genre').values_list('genre', flat=True).distinct()

    reading_count = sum(1 for s in user_shelves.values() if s == 'reading')
    want_count = sum(1 for s in user_shelves.values() if s == 'want_to_read')
    completed_count = sum(1 for s in user_shelves.values() if s == 'completed')

    stats = {
        'total': Book.objects.count(),
        'reading': reading_count,
        'want_to_read': want_count,
        'completed': completed_count,
    }

    context = {
        'books': page_obj,
        'page_obj': page_obj,
        'books_found': paginator.count,
        'filters_query': filters_query,
        'categories': categories,
        'genres': all_genres,
        'selected_category': category_slug,
        'selected_genre': genre,
        'selected_status': status_filter,
        'search_query': query,
        'sort_by': sort_by,
        'user_shelves': user_shelves,
        'user_shelves_json': json.dumps({str(k): v for k, v in user_shelves.items()}),
        'stats': stats,
    }
    return render(request, 'catalog/index.html', context)


def book_detail_view(request, book_id):
    """Детальна сторінка книги."""
    book = get_object_or_404(Book, id=book_id)
    return render(request, 'catalog/book_detail.html', {
        'book': book,
        'shelf_status': get_shelf_status(request, book.id),
        'shelf_choices': UserBookShelf.STATUS_CHOICES,
        'keywords_list': book.get_keywords_list(),
        'similar_books': similar_books(book),
    })


@require_POST
def add_book_view(request):
    """Додавання власної книги через модальне вікно."""
    title = request.POST.get('title', '').strip()
    author = request.POST.get('author', '').strip()
    if not (title and author):
        return redirect('catalog')
    cover_url = request.POST.get('cover_url', '').strip()
    description = request.POST.get('description', '').strip()
    book = Book.objects.create(
        title=title,
        author=author,
        genre=request.POST.get('genre') or 'Художня проза',
        category=None,
        year=parse_int(request.POST.get('year')),
        pages=parse_int(request.POST.get('pages')),
        description=description or 'Опис відсутній.',
        description_source='manual' if description else 'none',
        plot_keywords=request.POST.get('keywords', '').strip(),
        cover_url=cover_url or None,
    )
    index_book(book)
    return redirect('book_detail', book_id=book.id)


# ШІ: детектив сюжету, радник, чат

def detective_view(request):
    """Сторінка детективу сюжету (сам запит надсилається через identify_ajax_view)."""
    return render(request, 'catalog/detective.html')


@require_POST
def identify_ajax_view(request):
    """Детектив сюжету: визначає книгу за описом і перевіряє результат."""
    try:
        plot_description = json.loads(request.body).get('description', '').strip()
    except ValueError:
        plot_description = ''
    if not plot_description:
        return JsonResponse({'error': 'Потрібно надати опис сюжету'}, status=400)

    # в Gemini передаємо лише 20 найближчих книг, а не весь каталог
    catalog_text = catalog_for_prompt(search_books(plot_description, k=20), with_summary=True)
    try:
        result = verify_detective_result(identify_book_by_plot(plot_description, catalog_text))
    except AIUnavailable as e:
        return JsonResponse({'error': str(e)}, status=503)
    log_ai('plot_detective', plot_description, result)
    return JsonResponse(result)


def popular_genres(limit=10):
    """Найпоширеніші жанри каталогу (для кнопок ШІ-Радника)."""
    rows = (Book.objects.exclude(genre='').values('genre').annotate(n=Count('id'))
            .order_by('-n')[:limit])
    return [r['genre'] for r in rows]


def recommender_view(request):
    """ШІ-Радник: рекомендації за настроєм і темою. Працює і як звичайна форма, і через AJAX (JSON)."""
    is_json = 'application/json' in request.headers.get('content-type', '')
    data = {}
    if request.method == 'POST':
        try:
            data = json.loads(request.body) if is_json else request.POST
        except ValueError:
            data = {}
    mood = data.get('mood') or 'Затишок, спокій, рефлексія'
    genre = data.get('genre') or 'Будь-який'
    prompt = data.get('prompt', '')

    result, error = None, None
    if request.method == 'POST':
        try:
            candidates = search_books(f'{prompt}. Настрій: {mood}. Жанр: {genre}', k=30)
            result = verify_external_recommendations(get_ai_recommendations(
                prompt, mood, genre, catalog_for_prompt(candidates)))
            log_ai('recommendation', f'Настрій: {mood}, жанр: {genre}, запит: {prompt}', result)
            for rec in result['catalogRecommendations']:
                book = find_catalog_book(rec['bookId'])
                rec['book_obj'] = book  # для HTML-шаблону
                rec['book_id'] = book.id if book else None
                rec['genre'] = book.genre if book else None
        except AIUnavailable as e:
            error = str(e)

        if is_json:
            if result:
                for rec in result['catalogRecommendations']:
                    rec.pop('book_obj')  # об'єкт моделі не перетворюється в JSON
            return JsonResponse({**(result or {}), 'error': error})

    return render(request, 'catalog/recommender.html', {
        'result': result, 'error': error, 'prompt': prompt,
        'selected_mood': mood, 'selected_genre': genre, 'genres': ['Будь-який'] + popular_genres(),
    })


@require_POST
def chat_api_view(request):
    """Чат з ШІ-асистентом. Книги каталогу, згадані у відповіді, показуються картками."""
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'Некоректний запит'}, status=400)
    messages = data.get('messages', [])
    last_question = next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'), '')
    candidates = search_books(last_question, k=8) if last_question else []
    try:
        reply = send_chat_message(messages, data.get('currentBookContext'), catalog_for_prompt(candidates))
    except AIUnavailable as e:
        return JsonResponse({'error': str(e)}, status=503)
    log_ai('chat', last_question, reply)

    # книги з каталогу, які згадав асистент
    reply_lower = reply.lower()
    found = [{'id': b.id, 'title': b.title, 'author': b.author, 'cover_url': b.cover_url, 'genre': b.genre}
             for b in candidates if b.title.lower() in reply_lower]
    return JsonResponse({'reply': reply, 'found_catalog_books': found[:4]})


# Open Library

def open_library_view(request):
    """Пошук у світовій бібліотеці Open Library."""
    query = request.GET.get('q', '').strip()
    results = search_open_library_api(query) if query else []
    existing_titles = set(Book.objects.filter(title__in=[r['title'] for r in results])
                          .values_list('title', flat=True))
    return render(request, 'catalog/open_library.html', {
        'query': query, 'results': results, 'existing_titles': existing_titles,
    })


@require_POST
def import_open_library_book(request):
    """Імпорт книги з Open Library. Рік і сторінки - з Open Library; опис - з Open Library
    (переклад через ШІ), і лише за його відсутності - від ШІ."""
    title = request.POST.get('title', 'Без назви').strip()
    author = request.POST.get('author', 'Невідомий автор').strip()
    subjects = request.POST.get('subjects', '').strip()
    work_key = request.POST.get('key', '').strip()

    existing = Book.objects.filter(title=title, author=author).first()
    if existing and existing.description_source not in ('none', 'ai'):
        return redirect('book_detail', book_id=existing.id)  # книга вже є з нормальним описом, ШІ не потрібен

    ai_data, description, description_source = build_description(title, author, subjects, work_key)
    book, created = Book.objects.get_or_create(title=title, author=author, defaults={
        'year': parse_int(request.POST.get('year')),
        'pages': parse_int(request.POST.get('pages')),
        'cover_url': request.POST.get('cover_url', '').strip() or None,
        'isbn': request.POST.get('isbn', '').strip() or None,
        'genre': 'Світова література',
        'category': None,  # категорію визначить ШІ нижче
        'description': description,
        'description_source': description_source,
        'open_library_key': work_key or None,
        'source_url': f'https://openlibrary.org{work_key}' if work_key else None,
        'plot_keywords': subjects,
    })
    if created:
        apply_ai_classification(book, ai_data)
        book.save()
        index_book(book)
    elif book.description_source in ('none', 'ai') and description_source == 'open_library':
        # замінюємо опис від ШІ на справжній
        book.description, book.description_source = description, description_source
        book.open_library_key = work_key or book.open_library_key
        book.save()
        index_book(book)
    return redirect('book_detail', book_id=book.id)


# Полиця читача

SHELF_STATUSES = ('reading', 'want_to_read', 'completed', 'favorite')

# розділи сторінки полиці: статус, заголовок, іконка, колір
SHELF_SECTIONS = [
    ('reading', 'Читаю зараз', 'clock', 'sky'),
    ('want_to_read', 'У планах', 'bookmark', 'amber'),
    ('completed', 'Прочитано', 'check-circle', 'emerald'),
    ('favorite', 'Улюблене', 'heart', 'rose'),
]


def get_shelf_status(request, book_id):
    """Статус книги на полиці поточного користувача ('none', якщо книги там немає)."""
    if request.user.is_authenticated:
        entry = UserBookShelf.objects.filter(user=request.user, book_id=book_id).first()
        return entry.status if entry else 'none'
    return request.session.get('user_shelves', {}).get(str(book_id), 'none')


def bookshelf_view(request):
    """Особиста полиця читача: для авторизованих - з бази, для гостей - із сесії."""
    if request.user.is_authenticated:
        entries = list(UserBookShelf.objects.filter(user=request.user).select_related('book'))
    else:
        session_shelves = {parse_int(k): v for k, v in request.session.get('user_shelves', {}).items()}
        books = Book.objects.in_bulk([k for k in session_shelves if k])  # усі книги одним запитом
        entries = [type('ShelfItem', (), {'book': books[bid], 'status': st})()
                   for bid, st in session_shelves.items() if bid in books]

    shelves = {status: [e for e in entries if e.status == status] for status in SHELF_STATUSES}
    sections = [{'status': s, 'title': t, 'icon': i, 'color': c, 'items': shelves[s]}
                for s, t, i, c in SHELF_SECTIONS]
    return render(request, 'catalog/bookshelf.html', {
        **shelves,
        'sections': sections,
        'has_books': bool(entries),
        'shelf_choices': UserBookShelf.STATUS_CHOICES,
        # сторінки книг зі статусом «Прочитано» (у яких кількість сторінок відома)
        'total_pages': sum(e.book.pages or 0 for e in shelves['completed']),
    })


@require_POST
def update_shelf_status(request):
    """AJAX: зміна статусу книги на полиці."""
    try:
        data = json.loads(request.body)
        book = get_object_or_404(Book, id=parse_int(data.get('book_id')))
    except (ValueError, AttributeError):
        return JsonResponse({'error': 'Некоректний запит'}, status=400)
    new_status = data.get('status', 'none')
    if new_status not in SHELF_STATUSES + ('none',):
        return JsonResponse({'error': 'Невідомий статус'}, status=400)

    # 'none' - книгу прибрали з полиці
    if request.user.is_authenticated:
        if new_status == 'none':
            UserBookShelf.objects.filter(user=request.user, book=book).delete()
        else:
            UserBookShelf.objects.update_or_create(user=request.user, book=book, defaults={'status': new_status})
    else:
        shelves = request.session.get('user_shelves', {})
        if new_status == 'none':
            shelves.pop(str(book.id), None)
        else:
            shelves[str(book.id)] = new_status
        request.session['user_shelves'] = shelves
    return JsonResponse({'status': 'ok', 'new_status': new_status, 'book_id': book.id})
