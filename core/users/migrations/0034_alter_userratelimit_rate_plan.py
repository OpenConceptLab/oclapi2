# Generated by Django 4.2.16 on 2025-03-26 07:49

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0033_auto_20250318_0212'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userratelimit',
            name='rate_plan',
            field=models.CharField(choices=[('standard', 'standard'), ('guest', 'guest')], default='standard', max_length=100),
        ),
    ]
