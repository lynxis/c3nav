import re
import xml.etree.ElementTree as ET

from shapely.affinity import scale


class SVGImage:
    def __init__(self, width: int, height: int, scale: float=1):
        self.width = width
        self.height = height
        self.scale = scale
        self.g = ET.Element('g', {})
        self.defs = ET.Element('defs')
        self.def_i = 0

        blur_filter = ET.Element('filter', {'id': 'wallblur'})
        blur_filter.append(ET.Element('feGaussianBlur',
                                      {'in': 'SourceGraphic',
                                       'stdDeviation': str(int(0.7 * self.scale))}))
        self.defs.append(blur_filter)

    def get_element(self):
        root = ET.Element('svg', {
            'width': str(self.width*self.scale),
            'height': str(self.height*self.scale),
            'xmlns:svg': 'http://www.w3.org/2000/svg',
            'xmlns': 'http://www.w3.org/2000/svg',
            'xmlns:xlink': 'http://www.w3.org/1999/xlink',
        })
        root.append(self.defs)
        root.append(self.g)
        return root

    def get_xml(self):
        return ET.tostring(self.get_element()).decode()

    def new_defid(self):
        defid = 's'+str(self.def_i)
        self.def_i += 1
        return defid

    def _create_geometry(self, geometry):
        geometry = scale(geometry, xfact=1, yfact=-1, origin=(self.width / 2, self.height / 2))
        geometry = scale(geometry, xfact=self.scale, yfact=self.scale, origin=(0, 0))
        re_string = re.sub(r'([0-9]+)\.0', r'\1', re.sub(r'([0-9]+\.[0-9])[0-9]+', r'\1', geometry.svg(0, '#FFFFFF')))
        element = ET.fromstring(re_string)
        if element.tag != 'g':
            new_element = ET.Element('g')
            new_element.append(element)
            element = new_element
        paths = element.findall('polyline')
        if len(paths) == 0:
            paths = element.findall('path')
        for path in paths:
            path.attrib.pop('opacity', None)
            path.attrib.pop('fill', None)
            path.attrib.pop('fill-rule', None)
            path.attrib.pop('stroke', None)
            path.attrib.pop('stroke-width', None)
        return element

    def register_geometry(self, geometry, defid=None, as_clip_path=False, comment=None):
        if defid is None:
            defid = self.new_defid()

        element = self._create_geometry(geometry)

        if as_clip_path:
            element.tag = 'clipPath'
        element.set('id', defid)
        self.defs.append(element)
        return defid

    def add_clip_path(self, *geometries, inverted=False, subtract=False, defid=None):
        if defid is None:
            defid = self.new_defid()

        clippath = ET.Element('clipPath', {'id': defid})
        clippath.append(ET.Element('use', {'xlink:href': '#' + geometries[0]}))
        self.defs.append(clippath)
        return defid

    def add_geometry(self, geometry=None, fill_color=None, fill_opacity=None, opacity=None, filter=None,
                     stroke_width=0.0, stroke_color=None, stroke_opacity=None, stroke_linejoin=None, clip_path=None):
        if geometry is not None:
            if not geometry:
                return
            if isinstance(geometry, str):
                element = ET.Element('use', {'xlink:href': '#'+geometry})
            else:
                element = self._create_geometry(geometry)
        else:
            element = ET.Element('rect', {'width': '100%', 'height': '100%'})
        element.set('fill', fill_color or 'none')
        if fill_opacity:
            element.set('fill-opacity', str(fill_opacity))
        if stroke_width:
            element.set('stroke-width', str(stroke_width * self.scale))
        if stroke_color:
            element.set('stroke', stroke_color)
        if stroke_opacity:
            element.set('stroke-opacity', str(stroke_opacity))
        if stroke_linejoin:
            element.set('stroke-linejoin', str(stroke_linejoin))
        if opacity:
            element.set('opacity', str(opacity))
        if filter:
            element.set('filter', 'url(#'+filter+')')
        if clip_path:
            element.set('clip-path', 'url(#'+clip_path+')')

        self.g.append(element)
        return element
