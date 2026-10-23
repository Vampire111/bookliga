# БукЛіга

Каталог книг на Django з ШІ-асистентом.

Демо: https://bookliga.duckdns.org

## Що вміє

- каталог українських книг з анотаціями, категоріями, пошуком і фільтрами
- детектив сюжету: знаходить книгу за описом сюжету своїми словами
- ШІ-радник: підбирає книги під настрій і жанр
- чат з літературним асистентом
- схожі за змістом книги на сторінці книги
- пошук та імпорт книг з Open Library
- особиста полиця (хочу прочитати, читаю, прочитано, улюблене)

## Як це працює

Книги зібрані власним парсером з сайту «Книгарня Є» (парсер і повні дані в репозиторій не входять).

Для пошуку за змістом використовуються embeddings. Для кожної книги рахується вектор з 768 чисел (модель `gemini-embedding-001`) і зберігається в базі. Запит користувача теж перетворюється на вектор, і шукаються книги з найближчими векторами (косинусна близькість). Код у `catalog/semantic.py`.

Детектив, радник і чат працюють за схемою RAG: спочатку семантичним пошуком знаходяться найближчі книги (8 для чату, 20 для детективу, 30 для радника), і тільки вони передаються в Gemini разом із запитом. Відповіді детективу й радника приходять як JSON за схемою (Pydantic). Книги, які модель називає поза каталогом, перевіряються через Open Library, щоб не показувати вигадані.

Книгам без категорії вона визначається за схожими книгами: беруться 7 найближчих за змістом книг і обирається найчастіша категорія (k найближчих сусідів, команда `auto_categorize`). З параметром `--min-books N` команда також розформовує дрібні категорії і розподіляє їхні книги між більшими.

Якщо основна модель Gemini перевантажена або вичерпано її ліміт, запит повторюється на резервній моделі (`GEMINI_FALLBACK_MODEL`). Каталог і пошук за словами працюють і без ШІ.

## Технології

Python 3.10+, Django 5, SQLite, Gemini API (`google-genai`), numpy, Open Library API, Tailwind CSS.

## Запуск

```bash
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
```

Скопіювати `.env.example` в `.env` і вписати ключ Gemini (безкоштовно: https://aistudio.google.com/app/apikey).

```bash
python manage.py migrate
python manage.py loaddata demo_books   # 300 демо-книг разом з векторами
python manage.py createsuperuser   # для адмінки, необов'язково
python manage.py runserver
```

Демо-книги вже містять вектори, тому пошук і «схожі книги» працюють одразу. Вектори пораховані моделлю `gemini-embedding-001` (768 чисел); якщо змінити модель у `.env`, їх треба перерахувати: `python manage.py build_embeddings --rebuild`.

## Структура

```
bookliga/          налаштування Django
catalog/
  models.py        Book, Category, UserBookShelf, AiLog
  views.py         сторінки і API
  services.py      Gemini і Open Library
  semantic.py      семантичний пошук
  management/commands/
    build_embeddings, auto_categorize
  fixtures/        демо-книги (loaddata demo_books)
templates/         шаблони
```

## Розгортання на сервері

У `.env` на сервері: `DEBUG=False`, `ALLOWED_HOSTS=ваш-домен`, свій `DJANGO_SECRET_KEY`. Перед запуском: `python manage.py collectstatic`.

При `DEBUG=False` Django не віддає статичні файли, тому папку `staticfiles/` має роздавати вебсервер. На демо-сервері це Caddy, який також проксіює запити до Django і отримує HTTPS-сертифікат.
