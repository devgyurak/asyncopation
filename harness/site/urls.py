from django.urls import path

from harness.site import views

urlpatterns = [
    path("health/", views.health),
    path("sync/book/<int:pk>/", views.sync_book),
    path("sync/dashboard/", views.sync_dashboard),
    path("sync/books-with-authors/", views.sync_books_with_authors),
    path("sync/orders/", views.sync_create_order),
    path("core/book/<int:pk>/", views.core_book),
    path("core/dashboard/", views.core_dashboard),
    path("core/books-with-authors/", views.core_books_with_authors),
    path("core/orders/", views.core_create_order),
    path("aopt/book/<int:pk>/", views.aopt_book),
    path("aopt/dashboard/", views.aopt_dashboard),
    path("aopt/books-with-authors/", views.aopt_books_with_authors),
    path("aopt/orders/", views.aopt_create_order),
]
