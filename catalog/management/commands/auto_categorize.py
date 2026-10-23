"""Категорія для книг без категорії методом k найближчих сусідів:
беремо 7 найближчих за змістом книг і обираємо категорію, яка трапляється найчастіше.

З --min-books N спочатку розформовуються дрібні категорії (менше N книг),
а їхні книги розподіляються між великими тим самим способом.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import Book, Category
from catalog.semantic import categorized_neighbors


class Command(BaseCommand):
    help = 'Визначає категорію книгам без категорії за схожими книгами (kNN)'

    def add_arguments(self, parser):
        parser.add_argument('--k', type=int, default=7, help='кількість сусідів для голосування')
        parser.add_argument('--min-books', type=int, default=0,
                            help='розформувати категорії, у яких менше книг, ніж вказано')

    def handle(self, *args, **options):
        if options['min_books']:
            small = Category.objects.annotate(n=Count('books')).filter(n__lt=options['min_books'])
            names = [c.name for c in small]
            moved = Book.objects.filter(category__in=small).update(category=None)
            Category.objects.filter(id__in=[c.id for c in small]).delete()
            self.stdout.write(f'Розформовано категорій: {len(names)}, книг до перерозподілу: {moved}')

        books = list(Book.objects.filter(category=None).exclude(embedding=None))
        fixed = 0
        for book in books:
            neighbors = categorized_neighbors(book, options['k'])
            if not neighbors:
                continue
            category_id, votes = Counter(b.category_id for b in neighbors).most_common(1)[0]
            genres = Counter(b.genre for b in neighbors if b.category_id == category_id and b.genre)
            book.category_id = category_id
            if not book.genre and genres:
                book.genre = genres.most_common(1)[0][0]
            book.save(update_fields=['category', 'genre'])
            fixed += 1
            self.stdout.write(f'  «{book.title}» -> {book.category.name} / {book.genre} '
                              f'({votes} з {len(neighbors)} сусідів)')
        left = Book.objects.filter(category=None).count()
        self.stdout.write(self.style.SUCCESS(f'Визначено категорію для {fixed} книг. Без категорії лишилось: {left}.'))
        if left:
            self.stdout.write('Для книг без вектора спочатку запустіть: python manage.py build_embeddings')
