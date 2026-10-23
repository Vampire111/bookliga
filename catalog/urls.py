from django.urls import path
from . import views

urlpatterns = [
    path('', views.catalog_view, name='catalog'),
    path('book/<int:book_id>/', views.book_detail_view, name='book_detail'),
    path('detective/', views.detective_view, name='detective'),
    path('recommender/', views.recommender_view, name='recommender'),
    path('open-library/', views.open_library_view, name='open_library'),
    path('open-library/import/', views.import_open_library_book, name='import_book'),
    path('bookshelf/', views.bookshelf_view, name='bookshelf'),
    path('add-book/', views.add_book_view, name='add_book'),
    path('api/chat/', views.chat_api_view, name='chat_api'),
    path('api/identify-ajax/', views.identify_ajax_view, name='identify_ajax'),
    path('api/update-status/', views.update_shelf_status, name='update_status'),
]
