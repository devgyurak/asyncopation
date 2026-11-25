import django.contrib.postgres.fields
import django.contrib.postgres.fields.hstore
from django.contrib.postgres.operations import CITextExtension, HStoreExtension
from django.db import migrations, models

import harness.pgcatalog.models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        HStoreExtension(),
        CITextExtension(),
        migrations.CreateModel(
            name="PgFieldZoo",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("hstore", django.contrib.postgres.fields.hstore.HStoreField()),
                ("nullable_hstore", django.contrib.postgres.fields.hstore.HStoreField(null=True)),
                (
                    "hstore_list",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=django.contrib.postgres.fields.hstore.HStoreField(),
                        default=list,
                        size=None,
                    ),
                ),
                ("citext", harness.pgcatalog.models.CITextField()),
                (
                    "citext_list",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=harness.pgcatalog.models.CITextField(), default=list, size=None
                    ),
                ),
            ],
            options={"ordering": ["pk"]},
        ),
    ]
