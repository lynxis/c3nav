# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-06-09 13:00
from __future__ import unicode_literals

from shapely.geometry import Polygon

import c3nav.mapdata.fields
from django.db import migrations, models
import django.db.models.deletion


def create_columns(apps, schema_editor):
    Space = apps.get_model('mapdata', 'Space')
    for space in Space.objects.filter():
        if not space.geometry.interiors:
            continue
        for interior in space.geometry.interiors:
            space.columns.create(geometry=Polygon(list(interior.coords)))
        space.geometry = Polygon(list(space.geometry.exterior.coords))
        space.save()


class Migration(migrations.Migration):

    dependencies = [
        ('mapdata', '0008_auto_20170608_1317'),
    ]

    operations = [
        migrations.CreateModel(
            name='Column',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('geometry', c3nav.mapdata.fields.GeometryField(geomtype='polygon')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='columns', to='mapdata.Space', verbose_name='space')),
            ],
            options={
                'verbose_name': 'Column',
                'verbose_name_plural': 'Columns',
                'default_related_name': 'columns',
            },
        ),
        migrations.RunPython(create_columns),
    ]
