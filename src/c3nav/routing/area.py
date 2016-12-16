from itertools import combinations

import numpy as np
from matplotlib.path import Path

from c3nav.routing.utils.coords import coord_angle


class GraphArea():
    def __init__(self, room, mpl_clear, mpl_stairs, points=None):
        self.room = room
        self.graph = room.graph

        self.mpl_clear = mpl_clear
        self.mpl_stairs = mpl_stairs

        self.points = points

    def serialize(self):
        return (
            self.mpl_clear,
            self.mpl_stairs,
            self.points,
        )

    def prepare_build(self):
        self._built_points = []

    def build_connections(self):
        for point1, point2 in combinations(self._built_points, 2):
            path = Path(np.vstack((point1.xy, point2.xy)))

            # lies within room
            if self.mpl_clear.intersects_path(path):
                continue

            # stair checker
            angle = coord_angle(point1.xy, point2.xy)
            valid = True
            for stair_path, stair_angle in self.mpl_stairs:
                if not path.intersects_path(stair_path):
                    continue

                angle_diff = ((stair_angle - angle + 180) % 360) - 180
                up = angle_diff < 0  # noqa
                if not (40 < abs(angle_diff) < 150):
                    valid = False
                    break

            if not valid:
                continue

            point1.connect_to(point2)
            point2.connect_to(point1)

    def add_point(self, point):
        if not self.mpl_clear.contains_point(point.xy):
            return False
        self._built_points.append(point)
        return True

    def finish_build(self):
        self.points = np.array(tuple(point.i for point in self._built_points))