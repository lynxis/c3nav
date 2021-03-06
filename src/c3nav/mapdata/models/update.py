from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.utils.http import int_to_base36
from django.utils.timezone import make_naive
from django.utils.translation import ugettext_lazy as _


class MapUpdate(models.Model):
    """
    A map update. created whenever mapdata is changed.
    """
    datetime = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    type = models.CharField(max_length=32)

    class Meta:
        verbose_name = _('Map update')
        verbose_name_plural = _('Map updates')
        default_related_name = 'mapupdates'
        get_latest_by = 'datetime'

    @classmethod
    def last_update(cls):
        last_update = cache.get('mapdata:last_update', None)
        if last_update is not None:
            return last_update
        with cls.lock():
            last_update = cls.objects.latest()
            cache.set('mapdata:last_update', (last_update.pk, last_update.datetime), 900)
        return last_update.pk, last_update.datetime

    @classmethod
    def cache_key(cls):
        pk, dt = cls.last_update()
        return int_to_base36(pk)+'_'+int_to_base36(int(make_naive(dt).timestamp()))

    @classmethod
    @contextmanager
    def lock(cls):
        with transaction.atomic():
            yield cls.objects.select_for_update().earliest()

    def save(self, **kwargs):
        if self.pk is not None:
            raise TypeError
        super().save(**kwargs)
        cache.set('mapdata:last_update', (self.pk, self.datetime), 900)
