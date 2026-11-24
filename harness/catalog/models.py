"""Test model set covering every relation kind and the field types that differ between drivers.

Relations: FK (Book.author, Author.publisher), O2O (AuthorProfile.author), M2M (Book.tags),
reverse FK (Author.books), GenericForeignKey (Comment.target).
Composite primary key: BookEdition (book FK + number).
FieldZoo exists to catch per-driver type conversion differences (datetime/Decimal/JSON/UUID/bytes).
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Publisher(models.Model):
    name = models.CharField(max_length=100)
    country = models.CharField(max_length=2)

    class Meta:
        ordering = ["pk"]


class Author(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    birth_date = models.DateField(null=True)
    publisher = models.ForeignKey(
        Publisher, null=True, on_delete=models.SET_NULL, related_name="authors"
    )
    comments = GenericRelation("Comment", related_query_name="author")

    class Meta:
        ordering = ["pk"]


class AuthorProfile(models.Model):
    author = models.OneToOneField(Author, on_delete=models.CASCADE, related_name="profile")
    bio = models.TextField()
    website = models.URLField(blank=True)


class Tag(models.Model):
    name = models.SlugField(unique=True)

    class Meta:
        ordering = ["pk"]


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    tags = models.ManyToManyField(Tag, related_name="books")
    price = models.DecimalField(max_digits=8, decimal_places=2)
    pages = models.PositiveIntegerField()
    published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True)
    metadata = models.JSONField(default=dict)
    comments = GenericRelation("Comment", related_query_name="book")

    class Meta:
        ordering = ["pk"]


class BookEdition(models.Model):
    """Composite primary key (FK + integer)."""

    pk = models.CompositePrimaryKey("book", "number")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="editions")
    number = models.PositiveSmallIntegerField()
    isbn = models.CharField(max_length=13, unique=True)
    released = models.DateField(null=True)

    class Meta:
        ordering = ["pk"]


class Comment(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    body = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["pk"]
        indexes = [models.Index(fields=["content_type", "object_id"])]


class Order(models.Model):
    """Write-path benchmark/e2e target."""

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="orders")
    quantity = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class FieldZoo(models.Model):
    char = models.CharField(max_length=50)
    text = models.TextField()
    small = models.SmallIntegerField()
    integer = models.IntegerField()
    big = models.BigIntegerField()
    positive = models.PositiveIntegerField()
    floating = models.FloatField()
    decimal = models.DecimalField(max_digits=20, decimal_places=6)
    boolean = models.BooleanField()
    nullable_boolean = models.BooleanField(null=True)
    date = models.DateField()
    datetime = models.DateTimeField()
    time = models.TimeField()
    duration = models.DurationField()
    uuid = models.UUIDField()
    json = models.JSONField(null=True)
    binary = models.BinaryField()
    slug = models.SlugField()
    ip = models.GenericIPAddressField()
    nullable_char = models.CharField(max_length=10, null=True)

    class Meta:
        ordering = ["pk"]
