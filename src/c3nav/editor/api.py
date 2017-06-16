from itertools import chain

from rest_framework.decorators import list_route
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
from shapely.ops import cascaded_union

from c3nav.editor.models import ChangeSet


class EditorViewSet(ViewSet):
    """
    Editor API
    /geometries/ returns a list of geojson features, you have to specify ?level=<id> or ?space=<id>
    /geometrystyles/ returns styling information for all geometry types
    /changeset/ returns the current changeset
    """
    def _get_level_geometries(self, level):
        buildings = level.buildings.all()
        buildings_geom = cascaded_union([building.geometry for building in buildings])
        spaces = {space.id: space for space in level.spaces.all()}
        holes_geom = []
        for space in spaces.values():
            if space.outside:
                space.geometry = space.geometry.difference(buildings_geom)
            columns_geom = cascaded_union([column.geometry for column in space.columns.all()])
            space.geometry = space.geometry.difference(columns_geom)
            space_holes_geom = cascaded_union([hole.geometry for hole in space.holes.all()])
            holes_geom.append(space_holes_geom.intersection(space.geometry))
            space.geometry = space.geometry.difference(space_holes_geom)
        holes_geom = cascaded_union(holes_geom)

        for building in buildings:
            building.original_geometry = building.geometry
        for obj in chain(buildings, spaces.values()):
            obj.geometry = obj.geometry.difference(holes_geom)

        results = []
        results.extend(buildings)
        for door in level.doors.all():
            results.append(door)

        results.extend(spaces.values())
        return results

    def _get_levels_pk(self, request, level):
        Level = request.changeset.wrap('Level')
        levels_under = ()
        levels_on_top = ()
        lower_level = level.lower(Level).first()
        primary_levels = (level,) + ((lower_level,) if lower_level else ())
        secondary_levels = Level.objects.filter(on_top_of__in=primary_levels).values_list('pk', 'on_top_of')
        if lower_level:
            levels_under = tuple(pk for pk, on_top_of in secondary_levels if on_top_of == lower_level.pk)
        if True:
            levels_on_top = tuple(pk for pk, on_top_of in secondary_levels if on_top_of == level.pk)
        levels = chain([level.pk], levels_under, levels_on_top)
        return levels, levels_on_top, levels_under

    @list_route(methods=['get'])
    def geometries(self, request, *args, **kwargs):
        request.changeset = ChangeSet.get_for_request(request)

        Level = request.changeset.wrap('Level')
        Space = request.changeset.wrap('Space')

        level = request.GET.get('level')
        space = request.GET.get('space')
        if level is not None:
            if space is not None:
                raise ValidationError('Only level or space can be specified.')
            level = get_object_or_404(Level, pk=level)

            levels, levels_on_top, levels_under = self._get_levels_pk(request, level)
            levels = Level.objects.filter(pk__in=levels).prefetch_related('buildings', 'spaces', 'doors',
                                                                          'spaces__groups', 'spaces__holes',
                                                                          'spaces__columns')

            levels = {s.pk: s for s in levels}

            level = levels[level.pk]
            levels_under = [levels[pk] for pk in levels_under]
            levels_on_top = [levels[pk] for pk in levels_on_top]

            results = chain(
                *(self._get_level_geometries(s) for s in levels_under),
                self._get_level_geometries(level),
                *(self._get_level_geometries(s) for s in levels_on_top)
            )
            results = tuple(results)
            for result in results:
                print(type(result).__name__)

            return Response([obj.to_geojson(instance=obj) for obj in results])
        elif space is not None:
            space = get_object_or_404(Space.objects.select_related('level', 'level__on_top_of'), pk=space)
            level = space.level

            doors = [door for door in level.doors.all() if door.geometry.intersects(space.geometry)]
            doors_space_geom = cascaded_union([door.geometry for door in doors]+[space.geometry])

            levels, levels_on_top, levels_under = self._get_levels_pk(request, level.primary_level)
            other_spaces = Space.objects.filter(level__pk__in=levels).prefetch_related('groups')
            other_spaces = [s for s in other_spaces
                            if s.geometry.intersects(doors_space_geom) and s.pk != space.pk]

            space.bounds = True

            buildings = level.buildings.all()
            buildings_geom = cascaded_union([building.geometry for building in buildings])
            for other_space in other_spaces:
                if other_space.outside:
                    other_space.geometry = other_space.geometry.difference(buildings_geom)
                other_space.opacity = 0.4
                other_space.color = '#ffffff'
            for building in buildings:
                building.opacity = 0.5

            results = chain(
                buildings,
                doors,
                [space],
                space.areas.all().prefetch_related('groups'),
                space.holes.all(),
                space.stairs.all(),
                space.obstacles.all(),
                space.lineobstacles.all(),
                space.columns.all(),
                space.points.all().prefetch_related('groups'),
                other_spaces,
            )
            return Response(sum([self._get_geojsons(obj) for obj in results], ()))
        else:
            raise ValidationError('No level or space specified.')

    def _get_geojsons(self, obj):
        return (((obj.to_shadow_geojson(),) if hasattr(obj, 'to_shadow_geojson') else ()) +
                (obj.to_geojson(instance=obj),))

    @list_route(methods=['get'])
    def geometrystyles(self, request, *args, **kwargs):
        return Response({
            'building': '#929292',
            'space': '#d1d1d1',
            'hole': 'rgba(255, 0, 0, 0.3)',
            'door': '#ffffff',
            'area': 'rgba(85, 170, 255, 0.2)',
            'stair': 'rgba(160, 0, 160, 0.5)',
            'obstacle': '#999999',
            'lineobstacle': '#999999',
            'column': '#888888',
            'point': '#4488cc',
            'shadow': '#000000',
        })

    @list_route(methods=['get'])
    def changeset(self, request, *args, **kwargs):
        request.changeset = ChangeSet.get_for_request(request)

        return Response(request.changeset.serialize())
