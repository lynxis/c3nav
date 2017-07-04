from django.conf import settings
from django.db import models
from django.utils.translation import ugettext_lazy as _

from c3nav.editor.models import ChangeSet


class ChangeSetUpdate(models.Model):
    changeset = models.ForeignKey(ChangeSet, on_delete=models.CASCADE, related_name='updates')
    datetime = models.DateTimeField(auto_now_add=True, verbose_name=_('datetime'))
    comment = models.TextField(max_length=1000, null=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')

    state = models.CharField(null=None, db_index=True, choices=ChangeSet.STATES, max_length=20)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    title = models.CharField(max_length=100, null=True)
    description = models.TextField(max_length=1000, null=True)
    session_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    objects_changed = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Change set update')
        verbose_name_plural = _('Change set updates')
        ordering = ['datetime', 'pk']

    def __repr__(self):
        return '<Update #%s on ChangeSet #%s>' % (str(self.pk), str(self.changeset_id))
