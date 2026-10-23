"""Рахує embeddings для книг, у яких їх ще немає (--rebuild - для всіх).
Між запитами пауза через ліміти безкоштовного Gemini.
"""
import time

from django.core.management.base import BaseCommand

from catalog.models import Book
from catalog.semantic import book_text, to_bytes
from catalog.services import AIUnavailable, embed_texts


class Command(BaseCommand):
    help = 'Обчислює embeddings книг для семантичного пошуку'

    def add_arguments(self, parser):
        parser.add_argument('--rebuild', action='store_true', help='перерахувати вектори всіх книг')
        parser.add_argument('--batch', type=int, default=50, help='книг в одному запиті')
        parser.add_argument('--delay', type=float, default=2.0, help='пауза між запитами, с')

    def handle(self, *args, **options):
        books = Book.objects.all() if options['rebuild'] else Book.objects.filter(embedding=None)
        books = list(books.order_by('id'))
        total, done = len(books), 0
        self.stdout.write(f'Книг для обробки: {total}')

        for start in range(0, total, options['batch']):
            batch = books[start:start + options['batch']]
            for attempt in range(1, 4):
                try:
                    vectors = embed_texts([book_text(b) for b in batch], 'RETRIEVAL_DOCUMENT')
                    break
                except AIUnavailable as e:
                    if attempt == 3:
                        self.stderr.write(f'Зупинено: {e}. Запустіть команду пізніше - оброблені книги збережено.')
                        return
                    self.stdout.write(f'  помилка ({e}), повтор через {30 * attempt} с...')
                    time.sleep(30 * attempt)  # найчастіше це ліміт запитів - чекаємо

            for book, vector in zip(batch, vectors):
                book.embedding = to_bytes(vector)
            Book.objects.bulk_update(batch, ['embedding'])
            done += len(batch)
            self.stdout.write(f'  {done}/{total}')
            time.sleep(options['delay'])

        self.stdout.write(self.style.SUCCESS(f'Готово: оброблено {done} книг.'))
