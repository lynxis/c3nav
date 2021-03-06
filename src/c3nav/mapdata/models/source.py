import os

from django.conf import settings
from django.db import models
from django.utils.translation import ugettext_lazy as _

from c3nav.mapdata.models.base import BoundsMixin


class Source(BoundsMixin, models.Model):
    """
    A map source, images of levels that can be useful as backgrounds for the map editor
    """
    name = models.SlugField(_('Name'), unique=True, max_length=50)

    class Meta:
        verbose_name = _('Source')
        verbose_name_plural = _('Sources')
        default_related_name = 'sources'

    @property
    def filepath(self):
        return os.path.join(settings.SOURCES_ROOT, self.name)

    @property
    def title(self):
        return self.name

    def _serialize(self, level=True, **kwargs):
        result = super()._serialize(**kwargs)
        result['name'] = self.name
        return result
