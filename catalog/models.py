from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

class CategoryManager(models.Manager):
    def get_by_natural_key(self, slug):
        return self.get(slug=slug)


class Category(models.Model):
    objects = CategoryManager()

    slug = models.SlugField(max_length=50, unique=True, verbose_name="Слаґ (ID)")
    name = models.CharField(max_length=100, verbose_name="Назва категорії")
    description = models.TextField(blank=True, verbose_name="Опис категорії")

    class Meta:
        verbose_name = "Категорія"
        verbose_name_plural = "Категорії"
        ordering = ['name']

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.slug,)

class BookManager(models.Manager):
    def get_by_natural_key(self, source_url):
        return self.get(source_url=source_url)


class Book(models.Model):
    objects = BookManager()

    title = models.CharField(max_length=255, verbose_name="Назва книги")
    author = models.CharField(max_length=255, verbose_name="Автор")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="books", verbose_name="Категорія")
    genre = models.CharField(max_length=100, verbose_name="Жанр")
    year = models.IntegerField(null=True, blank=True, verbose_name="Рік")
    pages = models.IntegerField(null=True, blank=True, verbose_name="Кількість сторінок")
    isbn = models.CharField(max_length=50, blank=True, null=True, verbose_name="ISBN")
    publisher = models.CharField(max_length=255, blank=True, null=True, verbose_name="Видавництво")
    translator = models.CharField(max_length=255, blank=True, null=True, verbose_name="Перекладач")
    series = models.CharField(max_length=255, blank=True, null=True, verbose_name="Серія")
    cover_type = models.CharField(max_length=50, blank=True, null=True, verbose_name="Обкладинка (тверда/м'яка)")
    book_format = models.CharField(max_length=50, blank=True, null=True, verbose_name="Формат")
    extra_attributes = models.JSONField(default=dict, blank=True, verbose_name="Інші характеристики (з джерела)")
    language = models.CharField(max_length=50, default="Українська", verbose_name="Мова")
    cover_url = models.URLField(max_length=500, blank=True, null=True, verbose_name="URL обкладинки")
    description = models.TextField(verbose_name="Синопсис / Опис сюжету")
    plot_keywords = models.TextField(blank=True, help_text="Ключові слова сюжету через кому", verbose_name="Маркери сюжету")
    DESCRIPTION_SOURCES = [
        ('manual', 'Внесено вручну'),
        ('store', 'Книжковий магазин'),
        ('open_library', 'Open Library'),
        ('ai', 'Згенеровано ШІ'),
        ('none', 'Опис відсутній'),
    ]
    description_source = models.CharField(max_length=20, choices=DESCRIPTION_SOURCES, default='manual', verbose_name="Джерело опису")
    open_library_key = models.CharField(max_length=50, blank=True, null=True, verbose_name="Ключ Open Library (/works/...)")
    source_url = models.URLField(max_length=500, unique=True, blank=True, null=True, verbose_name="Посилання на джерело")
    # Вектор змісту книги для семантичного пошуку (масив float32, див. catalog/semantic.py)
    embedding = models.BinaryField(null=True, blank=True, editable=False, verbose_name="Embedding")
    created_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name="Дата додавання")

    class Meta:
        verbose_name = "Книга"
        verbose_name_plural = "Книги"
        ordering = ['title']

    def __str__(self):
        return f"«{self.title}» - {self.author}"

    def natural_key(self):
        return (self.source_url,)

    def get_keywords_list(self):
        if not self.plot_keywords:
            return []
        return [k.strip() for k in self.plot_keywords.split(',') if k.strip()]


class UserBookShelf(models.Model):
    STATUS_CHOICES = [
        ('none', 'Без статусу'),
        ('want_to_read', 'Хочу прочитати'),
        ('reading', 'Читаю зараз'),
        ('completed', 'Прочитано'),
        ('favorite', 'Улюблене'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="shelves", null=True, blank=True, verbose_name="Користувач")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="shelf_entries", verbose_name="Книга")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='none', verbose_name="Статус читання")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Останнє оновлення")

    class Meta:
        verbose_name = "Запис полиці"
        verbose_name_plural = "Книжкові полиці"

    def __str__(self):
        return f"{self.book.title} ({self.get_status_display()})"

class AiLog(models.Model):
    action_type = models.CharField(max_length=50, verbose_name="Тип запиту (identify/recommend/chat)")
    prompt_text = models.TextField(verbose_name="Запит користувача")
    ai_response = models.TextField(verbose_name="Відповідь Gemini")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Час запиту")

    class Meta:
        verbose_name = "Лог ШІ-запиту"
        verbose_name_plural = "Логи ШІ-запитів"
        ordering = ['-created_at']
