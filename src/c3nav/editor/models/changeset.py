import operator
import typing
from collections import OrderedDict
from contextlib import contextmanager
from functools import reduce
from itertools import chain

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import FieldDoesNotExist
from django.db import models, transaction
from django.urls import reverse
from django.utils.http import int_to_base36
from django.utils.timezone import make_naive
from django.utils.translation import ugettext_lazy as _
from django.utils.translation import ungettext_lazy
from rest_framework.exceptions import PermissionDenied

from c3nav.editor.models.changedobject import ChangedObject
from c3nav.editor.utils import is_created_pk
from c3nav.editor.wrappers import ModelWrapper
from c3nav.mapdata.models import LocationSlug, MapUpdate
from c3nav.mapdata.models.locations import LocationRedirect
from c3nav.mapdata.utils.models import get_submodels


class ChangeSet(models.Model):
    STATES = (
        ('unproposed', _('unproposed')),
        ('proposed', _('proposed')),
        ('review', _('in review')),
        ('rejected', _('rejected')),
        ('reproposed', _('proposed again')),
        ('finallyrejected', _('finally rejected')),
        ('applied', _('accepted and applied')),
    )
    created = models.DateTimeField(auto_now_add=True, verbose_name=_('created'))
    last_change = models.ForeignKey('editor.ChangeSetUpdate', null=True, related_name='+',
                                    verbose_name=_('last object change'))
    last_update = models.ForeignKey('editor.ChangeSetUpdate', null=True, related_name='+',
                                    verbose_name=_('last update'))
    last_state_update = models.ForeignKey('editor.ChangeSetUpdate', null=True, related_name='+',
                                          verbose_name=_('last state update'))
    state = models.CharField(max_length=20, db_index=True, choices=STATES, default='unproposed')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, verbose_name=_('Author'))
    title = models.CharField(max_length=100, default='', verbose_name=_('Title'))
    description = models.TextField(max_length=1000, default='', verbose_name=_('Description'))
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                    related_name='assigned_changesets', verbose_name=_('assigned to'))
    map_update = models.OneToOneField(MapUpdate, null=True, related_name='changeset', verbose_name=_('map update'))

    class Meta:
        verbose_name = _('Change Set')
        verbose_name_plural = _('Change Sets')
        default_related_name = 'changesets'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.changed_objects = None

        self.created_objects = {}
        self.updated_existing = {}
        self.deleted_existing = {}
        self.m2m_added = {}
        self.m2m_removed = {}

        self._object_changed = False
        self._request = None

    """
    Get Changesets for Request/Session/User
    """
    @classmethod
    def qs_for_request(cls, request):
        """
        Returns a base QuerySet to get only changesets the current user is allowed to see
        """
        if request.user.is_authenticated:
            return ChangeSet.objects.filter(author=request.user)
        elif 'changeset' in request.session:
            return ChangeSet.objects.filter(pk=request.session['changeset'])
        return ChangeSet.objects.none()

    @classmethod
    def get_for_request(cls, request, select_related=None):
        """
        Get the changeset for the current request.
        If a changeset is associated with the session id, it will be returned.
        Otherwise, if the user is authenticated, the last created queryset
        for this user will be returned and the session id will be added to it.
        If both fails, an empty unsaved changeset will be returned which will
        be automatically saved when a change is added to it.
        In any case, the default autor for changes added to the queryset during
        this request will be set to the current user.
        """
        if select_related is None:
            select_related = ('last_change', )
        changeset_pk = request.session.get('changeset')
        if changeset_pk is not None:
            qs = ChangeSet.objects.select_related(*select_related).exclude(state__in=('applied', 'finallyrejected'))
            if request.user.is_authenticated:
                qs = qs.filter(author=request.user)
            else:
                qs = qs.filter(author__isnull=True)
            try:
                return qs.get(pk=changeset_pk)
            except ChangeSet.DoesNotExist:
                pass

        changeset = ChangeSet()
        changeset._request = request

        if request.user.is_authenticated:
            changeset.author = request.user

        return changeset

    """
    Wrap Objects
    """
    def wrap_model(self, model):
        if isinstance(model, str):
            model = apps.get_model('mapdata', model)
        assert isinstance(model, type) and issubclass(model, models.Model)
        return ModelWrapper(self, model)

    def wrap_instance(self, instance):
        assert isinstance(instance, models.Model)
        return self.wrap_model(instance.__class__).create_wrapped_model_class()(self, instance)

    def relevant_changed_objects(self):
        return self.changed_objects_set.exclude(existing_object_pk__isnull=True, deleted=True)

    def fill_changes_cache(self, include_deleted_created=False):
        """
        Get all changed objects and fill this ChangeSet's changes cache.
        Only executable once, if something is changed later the cache will be automatically updated.
        This method gets called automatically when the cache is needed.
        Only call it if you need to set include_deleted_created to True.
        :param include_deleted_created: Fetch created objects that were deleted.
        :rtype: True if the method was executed, else False
        """
        if self.changed_objects is not None:
            return False

        if self.pk is None:
            self.changed_objects = {}
            return False

        cache_key = self.cache_key_by_changes + ':cache'

        if include_deleted_created:
            cache_key += '_with_deleted'
            qs = self.changed_objects_set.all()
        else:
            qs = self.relevant_changed_objects()

        cached_cache = cache.get(cache_key)
        if cached_cache is not None:
            (self.changed_objects, self.created_objects, self.updated_existing,
             self.deleted_existing, self.m2m_added, self.m2m_removed) = cached_cache
            return True

        if self.state != 'applied':
            self._clean_changes()

        self.changed_objects = {}
        for change in qs:
            change.update_changeset_cache()

        cache.set(cache_key, (self.changed_objects, self.created_objects, self.updated_existing,
                              self.deleted_existing, self.m2m_added, self.m2m_removed), 300)

        return True

    def iter_changed_objects(self) -> typing.Iterable[ChangedObject]:
        return chain(*(changed_objects.values() for changed_objects in self.changed_objects.values()))

    def _clean_changes(self):
        changed_objects = self.changed_objects_set.all()
        with self.lock_to_edit():
            # delete changed objects that refer in some way to deleted objects and clean up m2m changes
            object_pks = {}
            for changed_object in changed_objects:
                changed_object.add_relevant_object_pks(object_pks)

            to_save = set()

            deleted_object_pks = {}
            for model, pks in object_pks.items():
                pks = set(pk for pk in pks if not is_created_pk(pk))
                deleted_object_pks[model] = pks - set(model.objects.filter(pk__in=pks).values_list('pk', flat=True))

            for changed_object in changed_objects:
                if changed_object.handle_deleted_object_pks(deleted_object_pks):
                    to_save.add(changed_object)

            # remove deleted objects
            changed_objects = [obj for obj in changed_objects if obj.pk is not None]

            # clean updated fields
            objects = self.get_objects(many=False, changed_objects=changed_objects, prefetch_related=('groups', ))
            for changed_object in changed_objects:
                if changed_object.clean_updated_fields(objects):
                    to_save.add(changed_object)

            # clean m2m
            for changed_object in changed_objects:
                if changed_object.clean_m2m(objects):
                    to_save.add(changed_object)

            # remove duplicate slugs
            slugs = set()
            for changed_object in changed_objects:
                if issubclass(changed_object.model_class, LocationSlug):
                    slug = changed_object.updated_fields.get('slug', None)
                    if slug is not None:
                        if slug in slugs:
                            changed_object.updated_fields.pop('slug', None)
                            to_save.add(changed_object)
                        else:
                            slugs.add(slug)

            existing_slugs = set(LocationSlug.objects.filter(slug__in=slugs).values_list('slug', flat=True))

            for changed_object in changed_objects:
                if issubclass(changed_object.model_class, LocationSlug):
                    if changed_object.updated_fields.get('slug', None) in existing_slugs:
                        if issubclass(changed_object.model_class, LocationRedirect):
                            to_save.discard(changed_object)
                            changed_object.delete()
                        else:
                            changed_object.updated_fields.pop('slug', None)
                            to_save.add(changed_object)

            for changed_object in to_save:
                changed_object.save(standalone=True)

    """
    Analyse Changes
    """
    def get_objects(self, many=True, changed_objects=None, prefetch_related=()):
        if changed_objects is None:
            if self.changed_objects is None:
                raise TypeError
            changed_objects = self.iter_changed_objects()

        # collect pks of relevant objects
        object_pks = {}
        for change in changed_objects:
            change.add_relevant_object_pks(object_pks, many=many)

        slug_submodels = tuple(model for model in object_pks.keys() if issubclass(model, LocationSlug))
        object_pks[LocationSlug] = reduce(operator.or_, (object_pks[model] for model in slug_submodels))
        for model in slug_submodels:
            object_pks.pop(model)

        # retrieve relevant objects
        objects = {}
        for model, pks in object_pks.items():
            if not pks:
                continue
            created_pks = set(pk for pk in pks if is_created_pk(pk))
            existing_pks = pks - created_pks
            model_objects = {}
            if existing_pks:
                qs = model.objects.filter(pk__in=existing_pks)
                for prefetch in prefetch_related:
                    try:
                        model._meta.get_field(prefetch)
                    except FieldDoesNotExist:
                        pass
                    else:
                        qs = qs.prefetch_related(prefetch)
                for obj in model.objects.filter(pk__in=existing_pks):
                    if model == LocationSlug:
                        obj = obj.get_child()
                    model_objects[obj.pk] = obj
            if created_pks:
                for pk in created_pks:
                    model_objects[pk] = self.get_created_object(model, pk, allow_deleted=True)._obj
            objects[model] = model_objects

        # add LocationSlug objects as their correct model
        for pk, obj in objects.get(LocationSlug, {}).items():
            objects.setdefault(obj.__class__, {})[pk] = obj

        return objects

    def get_changed_values(self, model: models.Model, name: str) -> tuple:
        """
        Get all changes values for a specific field on existing models
        :param model: model class
        :param name: field name
        :return: returns a dictionary with primary keys as keys and new values as values
        """
        r = tuple((pk, values[name]) for pk, values in self.updated_existing.get(model, {}).items() if name in values)
        return r

    def get_changed_object(self, obj) -> ChangedObject:
        model = obj.__class__
        pk = obj.pk
        if pk is None:
            return ChangedObject(changeset=self, model_class=model)

        self.fill_changes_cache()

        objects = tuple(obj for obj in ((submodel, self.changed_objects.get(submodel, {}).get(pk, None))
                                        for submodel in get_submodels(model)) if obj[1] is not None)
        if len(objects) > 1:
            raise model.MultipleObjectsReturned
        if objects:
            return objects[0][1]

        if is_created_pk(pk):
            raise model.DoesNotExist

        return ChangedObject(changeset=self, model_class=model, existing_object_pk=pk)

    def get_created_object(self, model, pk, get_foreign_objects=False, allow_deleted=False):
        """
        Gets a created model instance.
        :param model: model class
        :param pk: primary key
        :param get_foreign_objects: whether to fetch foreign objects and not just set their id to field.attname
        :param allow_deleted: return created objects that have already been deleted (needs get_history=True)
        :return: a wrapped model instance
        """
        self.fill_changes_cache()
        if issubclass(model, ModelWrapper):
            model = model._obj

        obj = self.get_changed_object(model(pk=pk))
        if obj.deleted and not allow_deleted:
            raise model.DoesNotExist
        return obj.get_obj(get_foreign_objects=get_foreign_objects)

    def get_created_pks(self, model) -> set:
        """
        Returns a set with the primary keys of created objects from this model
        """
        self.fill_changes_cache()
        if issubclass(model, ModelWrapper):
            model = model._obj
        return set(self.created_objects.get(model, {}).keys())

    """
    Permissions
    """
    @property
    def changes_editable(self):
        return self.state in ('unproposed', 'rejected', 'review')

    @property
    def proposed(self):
        return self.state not in ('unproposed', 'rejected')

    @property
    def closed(self):
        return self.state in ('finallyrejected', 'applied')

    def is_author(self, request):
        return (self.author == request.user or (self.author is None and not request.user.is_authenticated and
                                                request.session.get('changeset', None) == self.pk))

    def can_see(self, request):
        return self.is_author(request)

    @contextmanager
    def lock_to_edit(self, request=None):
        with transaction.atomic():
            if self.pk is not None:
                changeset = ChangeSet.objects.select_for_update().get(pk=self.pk)
                if request is not None and not changeset.can_edit(request):
                    raise PermissionDenied

                self._object_changed = False
                yield changeset
                if self._object_changed and request is not None:
                    update = changeset.updates.create(user=request.user if request.user.is_authenticated else None,
                                                      objects_changed=True)
                    changeset.last_update = update
                    changeset.last_change = update
                    changeset.save()
            else:
                yield

    def can_edit(self, request):
        if not self.proposed:
            return self.is_author(request)
        elif self.state == 'review':
            return self.assigned_to == request.user
        return False

    def can_delete(self, request):
        return self.can_edit(request) and self.state == 'unproposed'

    def can_propose(self, request):
        return self.can_edit(request) and not self.proposed and self.changed_objects_count

    def can_unpropose(self, request):
        return self.author_id == request.user.pk and self.state in ('proposed', 'reproposed')

    def can_review(self, request):
        # todo implement permissions
        return self.is_author(request)

    def can_start_review(self, request):
        return self.can_review(request) and self.state in ('proposed', 'reproposed')

    def can_end_review(self, request):
        return self.can_review(request) and self.state == 'review' and self.assigned_to == request.user

    def can_unreject(self, request):
        return (self.can_review(request) and self.state in ('rejected', 'finallyrejected') and
                self.assigned_to == request.user)

    """
    Update methods
    """
    def propose(self, user):
        new_state = {'unproposed': 'proposed', 'rejected': 'reproposed'}[self.state]
        update = self.updates.create(user=user, state=new_state)
        self.state = new_state
        self.last_update = update
        self.last_state_update = update
        self.save()

    def unpropose(self, user):
        new_state = {'proposed': 'unproposed', 'reproposed': 'rejected'}[self.state]
        update = self.updates.create(user=user, state=new_state)
        self.state = new_state
        self.last_update = update
        self.last_state_update = update
        self.save()

    def start_review(self, user):
        assign_to = user
        if self.assigned_to == user:
            assign_to = None
        else:
            self.assigned_to = user

        if self.state != 'review':
            update = self.updates.create(user=user, state='review', assigned_to=assign_to)
            self.state = 'review'
            self.last_state_update = update
        elif assign_to is None:
            return
        else:
            update = self.updates.create(user=user, assigned_to=assign_to)

        self.last_update = update
        self.save()

    def reject(self, user, comment: str, final: bool):
        state = 'finallyrejected' if final else 'rejected'
        update = self.updates.create(user=user, state=state, comment=comment)
        self.state = state
        self.last_state_update = update
        self.last_update = update
        self.save()

    def unreject(self, user):
        update = self.updates.create(user=user, state='review')
        self.state = 'review'
        self.last_state_update = update
        self.last_update = update
        self.save()

    def apply(self, user):
        update = self.updates.create(user=user, state='applied')
        map_update = MapUpdate.objects.create(user=user, type='changeset')
        self.state = 'applied'
        self.last_state_update = update
        self.last_update = update
        self.map_update = map_update
        self.save()

    def activate(self, request):
        request.session['changeset'] = self.pk

    """
    Methods for display
    """
    @property
    def changed_objects_count(self):
        """
        Get the number of changed objects.
        """
        self.fill_changes_cache()
        count = 0
        changed_locationslug_pks = set()
        for model, objects in self.changed_objects.items():
            if issubclass(model, LocationSlug):
                if model == LocationRedirect:
                    continue
                changed_locationslug_pks.update(objects.keys())
            count += sum(1 for obj in objects.values() if not obj.is_created or not obj.deleted)

        count += len(set(obj.updated_fields['target']
                         for obj in self.changed_objects.get(LocationRedirect, {}).values()) - changed_locationslug_pks)
        return count

    @property
    def count_display(self):
        """
        Get “%d changed objects” display text.
        """
        if self.pk is None:
            return _('No objects changed')
        return (ungettext_lazy('%(num)d object changed', '%(num)d objects changed', 'num') %
                {'num': self.changed_objects_count})

    @property
    def last_update_cache_key(self):
        last_update = self.created if self.last_update_id is None else self.last_update.datetime
        return (int_to_base36(self.last_update_id or 0)+'_'+int_to_base36(int(make_naive(last_update).timestamp())))

    @property
    def last_change_cache_key(self):
        last_change = self.created if self.last_change_id is None else self.last_change.datetime
        return (int_to_base36(self.last_change_id or 0)+'_'+int_to_base36(int(make_naive(last_change).timestamp())))

    @property
    def cache_key_by_changes(self):
        return ':'.join(('editor:changeset', str(self.pk), MapUpdate.cache_key(), self.last_change_cache_key))

    def get_absolute_url(self):
        if self.pk is None:
            return ''
        return reverse('editor.changesets.detail', kwargs={'pk': self.pk})

    def serialize(self):
        return OrderedDict((
            ('id', self.pk),
            ('author', self.author_id),
            ('state', self.state),
            ('assigned_to', self.assigned_to_id),
            ('changed_objects_count', self.changed_objects_count),
            ('created', None if self.created is None else self.created.isoformat()),
            ('last_change', None if self.last_change is None else self.last_change.datetime.isoformat()),
            ('last_update', None if self.last_update is None else self.last_update.datetime.isoformat()),
            ('last_state_update', (None if self.last_state_update is None else
                                   self.last_state_update.datetime.isoformat())),
            ('last_state_update_user', (None if self.last_state_update is None else
                                        self.last_state_update.user_id)),
            ('last_state_update_comment', (None if self.last_state_update is None else
                                           self.last_state_update.comment)),
        ))

    def save(self, *args, **kwargs):
        if self.state == 'applied':
            raise TypeError('Applied change sets can not be edited.')
        super().save(*args, **kwargs)
        if self._request is not None:
            self.activate(self._request)

    STATE_ICONS = {
        'unproposed': 'pencil',
        'proposed': 'send',
        'reproposed': 'send',
        'review': 'hourglass',
        'rejected': 'remove',
        'finallyrejected': 'remove',
        'applied': 'ok',
    }

    @property
    def icon(self):
        return self.STATE_ICONS[self.state]

    STATE_STYLES = {
        'unproposed': 'muted',
        'proposed': 'info',
        'reproposed': 'info',
        'review': 'info',
        'rejected': 'danger',
        'finallyrejected': 'danger',
        'applied': 'success',
    }

    @property
    def style(self):
        return self.STATE_STYLES[self.state]
