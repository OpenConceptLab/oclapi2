from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import MD5, Upper

import core.common.db_functions


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('common', '0004_create_ext_unaccent'),
        ('concepts', '0087_conceptname_md5_upper_name_locale'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='conceptname',
            index=models.Index(
                MD5(Upper(core.common.db_functions.ImmutableUnaccent('name'))), 'locale',
                name='concept_nam_md5_unacc_loc_idx',
                condition=models.Q(retired=False),
            ),
        ),
        RemoveIndexConcurrently(
            model_name='conceptname',
            name='concept_nam_md5_upper_loc_idx',
        ),
    ]
