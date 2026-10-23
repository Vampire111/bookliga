from django.contrib import admin
from .models import Category, Book, UserBookShelf, AiLog

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'book_count')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}

    def book_count(self, obj):
        return obj.books.count()
    book_count.short_description = "Кількість книг"

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'category', 'genre', 'year', 'pages')
    list_filter = ('category', 'description_source')
    search_fields = ('title', 'author', 'description', 'plot_keywords', 'isbn')
    ordering = ('-created_at',)

    def save_model(self, request, obj, form, change):
        """Після редагування назви, автора, жанру чи опису в адмінці оновлюємо вектор для пошуку."""
        super().save_model(request, obj, form, change)
        if not change or {'title', 'author', 'genre', 'description', 'plot_keywords'} & set(form.changed_data):
            from .semantic import index_book
            index_book(obj)
    fieldsets = (
        ('Основна інформація', {
            'fields': ('title', 'author', 'category', 'genre', 'year', 'language')
        }),
        ('Видання', {
            'fields': ('publisher', 'translator', 'series', 'cover_type', 'book_format', 'extra_attributes')
        }),
        ('Зміст і сюжет', {
            'fields': ('description', 'description_source', 'plot_keywords')
        }),
        ('Технічні деталі та обкладинка', {
            'fields': ('pages', 'isbn', 'cover_url', 'source_url', 'open_library_key')
        }),
    )

@admin.register(UserBookShelf)
class UserBookShelfAdmin(admin.ModelAdmin):
    list_display = ('book', 'user', 'status', 'updated_at')
    list_filter = ('status',)
    search_fields = ('book__title', 'book__author')

@admin.register(AiLog)
class AiLogAdmin(admin.ModelAdmin):
    list_display = ('action_type', 'created_at', 'short_prompt')
    list_filter = ('action_type', 'created_at')
    search_fields = ('prompt_text', 'ai_response')
    readonly_fields = ('action_type', 'prompt_text', 'ai_response', 'created_at')

    def short_prompt(self, obj):
        return (obj.prompt_text[:80] + '...') if len(obj.prompt_text) > 80 else obj.prompt_text
    short_prompt.short_description = "Запит"
