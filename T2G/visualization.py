import vtk
from qgis.core import Qgis, QgsFeature, QgsGeometry, QgsWkbTypes, QgsMessageLog, QgsVectorDataProvider, \
    QgsVectorLayerUtils, QgsVectorLayer, QgsExpressionContextUtils, QgsProject, QgsRenderContext
from qgis.gui import QgsAttributeDialog
from qgis.utils import iface
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import qApp

from random import random
from .AnchorUpdater import VtkAnchorUpdater
from os.path import basename

COLOUR_SPACE = ['blanched_almond',
                'blue_medium',
                'flesh_ochre',
                'turquoise_dark',
                'cadmium_lemon',
                'sky_blue_light',
                'permanent_red_violet',
                'slate_grey_dark',
                'deep_pink',
                'olive_drab',
                'cinnabar_green',
                'manganese_blue',
                'saddle_brown',
                ]

CLOUD_MARKER = "⛅ "

# todo: iface.activeLayer().renderer().symbols(QgsRenderContext())[0].color().getHsl()
class ColourProvider:
    index = 0
    colours = COLOUR_SPACE

    def next(self):
        self.index += 1
        if self.index > len(self.colours) - 1:
            return vtk.vtkColor3d(random(), random(), random())
        else:
            return vtk.vtkNamedColors().GetColor3d(COLOUR_SPACE[self.index])


class SimpleRingBuffer(list):
    def __getitem__(self, item):
        item = item % len(self)
        return super(SimpleRingBuffer, self).__getitem__(item)

    def slices(self, one_end, other_end):
        """Produces two slices delimited by the two items one_end and other_end.
        Both slices contain both ends.
        """
        i_start = self.index(one_end)
        i_end = self.index(other_end)
        # we have the indices, let's sort them
        left = min(i_start, i_end)
        right = max(i_start, i_end)
        width = right - left
        # we get a copy of our items that starts at the left end
        shifted = [self[i] for i in range(left, left + len(self))]
        # And split it at the right end
        inner = shifted[:width + 1]
        outer = shifted[width:]
        outer.append(shifted[0])

        return inner, outer

    def slice(self, one_end, between, other_end):
        cw, ccw = self.slices(one_end, other_end)
        return cw if between in cw else ccw


class VtkLayer:
    vtkActor = None
    pickable_actor = None

    def __init__(self, qgs_layer):
        self.source_layer = qgs_layer
        self.id = self.source_layer.id()
        self.geoType = self.source_layer.geometryType()
        self.wkbTypeName = QgsWkbTypes.displayString(self.source_layer.wkbType())
        self.isMulti = QgsWkbTypes.isMultiType(self.source_layer.wkbType())
        self.extractor = VtkAnchorUpdater(layer=self.source_layer, geoType=self.geoType)
        # self.get_feature = self.extractor.features.get_common
        self.anchors = self.extractor.anchors
        # self.geometries = self.extractor.geometries
        self.poly_data = self.extractor.poly_data
        # glyph3D object (cross)
        self.glyphPt = vtk.vtkPoints()
        self.glyphPt.SetDataTypeToDouble()
        self.glyphCells = vtk.vtkCellArray()
        self.glyphPts = [(-0.05, 0.05, 0.05), (0.05, -0.05, -0.05),
                         (-0.05, -0.05, 0.05), (0.05, 0.05, -0.05),
                         (0.05, 0.05, 0.05), (-0.05, -0.05, -0.05),
                         (0.05, -0.05, 0.05), (-0.05, 0.05, -0.05)]
        self.glyphData = vtk.vtkPolyData()
        self.glyphData.SetPoints(self.glyphPt)
        self.glyphData.SetLines(self.glyphCells)

        index = 0
        for lines in range(4):
            glyphLine = vtk.vtkPolyLine()
            for point in range(index, index+2):
                glyphLine.GetPointIds().InsertNextId(index)
                self.glyphPt.InsertNextPoint(self.glyphPts[index])
                index += 1
            self.glyphCells.InsertNextCell(glyphLine)

    def set_pickability(self, pickable):
        self.pickable_actor.PickableOn() if pickable else self.pickable_actor.PickableOff()
        setattr(self.pickable_actor, "features", self.extractor.features)

    def set_highlight(self, highlighted):
        raise NotImplementedError("Has to be implemented for each derived class")

    def update(self):
        self.poly_data = self.extractor.startExtraction()

    def add_feature(self, vertices):
        capabilities = self.source_layer.dataProvider().capabilities()
        if not capabilities & QgsVectorDataProvider.AddFeatures:
            QgsMessageLog.logMessage('data provider incapable')
            return -1
        if "Poly" in self.wkbTypeName and len(vertices) < 3:
            iface.messageBar().pushMessage("Fehler: ", "Polygone müssen mindestens 3 Punkte haben!", Qgis.Warning, 5)
            return -1
        if "LineString" in self.wkbTypeName and len(vertices) <= 1:
            iface.messageBar().pushMessage("Fehler: ", "Linien müssen mindestens 2 Punkte haben!", Qgis.Warning, 5)
            return -1
        if isinstance(self, VtkPointCloudLayer):
            iface.messageBar().pushMessage("Fehler: ", "Schreiben in PointCloud nicht möglich!", Qgis.Warning, 5)
            return -1
        wktGeo = self.make_wkt(vertices)
        if isinstance(wktGeo, list):
            # This only happens for multiple single point geos ->
            # It has to be moved there as soon as they have their
            # own VtkLayer type.
            features = []
            for geo in wktGeo:
                features.append(QgsVectorLayerUtils.createFeature(self.source_layer,
                                                                  QgsGeometry.fromWkt(geo),
                                                                  {},
                                                                  self.source_layer.createExpressionContext()))
        else:
            geometry = QgsGeometry.fromWkt(wktGeo)
            features = [QgsVectorLayerUtils.createFeature(self.source_layer,
                                                          geometry,
                                                          {},
                                                          self.source_layer.createExpressionContext())]
        nextFeatId = self.source_layer.featureCount()
        for feat in features:
            feat.setId(nextFeatId)
            self.source_layer.startEditing()
            if QgsAttributeDialog(self.source_layer, feat, False).exec_():
                self.source_layer.dataProvider().addFeatures([feat])
                self.source_layer.featureAdded.emit(nextFeatId)
                QgsMessageLog.logMessage('Feature added')
                self.source_layer.commitChanges()
                nextFeatId += 1  # next id for multiple points
            else:
                QgsMessageLog.logMessage('layer rolled back')
                self.source_layer.rollBack()


class MixinSingle:
    def make_wkt(self, vertices):
        if "Poly" in self.wkbTypeName:
            vertices.append(vertices[0])
        vertexts = self.make_vertexts(vertices)
        wkt = f"{self.wkbTypeName} (({', '.join(vertexts)}))"
        return wkt


class MixinMulti:
    def make_wkt(self, vertices):
        if "Poly" in self.wkbTypeName:
            vertices.append(vertices[0])
        vertexts = self.make_vertexts(vertices)
        wkt = f"{self.wkbTypeName} ((({', '.join(vertexts)})))"
        return wkt


class Mixin2D:
    def make_vertexts(self, vertices):
        return [f'{v[0]} {v[1]}' for v in vertices]


class Mixin3D:
    def make_vertexts(self, vertices):
        return [f'{v[0]} {v[1]} {v[2]}' for v in vertices]


class MixinZM:
    def make_vertexts(self, vertices):
        return [f'{v[0]} {v[1]} {v[2]} {0.0}' for v in vertices]


class MixinM:
    def make_vertexts(self, vertices):
        return [f'{v[0]} {v[1]} {0.0}' for v in vertices]


class VtkPointCloudLayer(VtkLayer):
    def __init__(self, cloud_file_name, qgis_layer):
        cellIndex = 0
        points = vtk.vtkPoints()
        points.SetDataTypeToDouble()
        cells = vtk.vtkCellArray()
        colors = vtk.vtkUnsignedCharArray()
        colors.SetNumberOfComponents(3)
        super().__init__(qgis_layer)

        with open(cloud_file_name, 'r', encoding="utf-8-sig") as file:
            for line in file:
                qApp.processEvents()
                split = line.split()
                pid = points.InsertNextPoint((float(split[0]), float(split[1]), float(split[2])))
                cells.InsertNextCell(1, [pid])
                colors.InsertTuple3(cellIndex, int(split[3]), int(split[4]), int(split[5]))
                cellIndex += 1
        polyData = vtk.vtkPolyData()
        polyData.SetPoints(points)
        polyData.SetVerts(cells)
        polyData.GetPointData().SetScalars(colors)
        pointMapper = vtk.vtkPolyDataMapper()
        pointMapper.SetInputData(polyData)
        pointMapper.Update()
        pointActor = vtk.vtkActor()
        pointActor.SetMapper(pointMapper)
        pointActor.PickableOff()

        self.vtkActor = pointActor
        self.pickable_actor = pointActor
        self.id = qgis_layer.id()

    def set_highlight(self, highlighted):
        return


class VtkPolyLayer(MixinSingle, Mixin2D, VtkLayer):
    def get_actors(self, colour):
        poly_data = self.extractor.startExtraction()

        poly_mapper = vtk.vtkPolyDataMapper()
        tri_filter = vtk.vtkTriangleFilter()
        tri_filter.SetInputData(poly_data)
        tri_filter.Update()

        # use vtkFeatureEdges for Boundary rendering
        featureEdges = vtk.vtkFeatureEdges()
        featureEdges.SetColoring(0)
        featureEdges.BoundaryEdgesOn()
        featureEdges.FeatureEdgesOn()
        featureEdges.ManifoldEdgesOff()
        featureEdges.NonManifoldEdgesOff()
        featureEdges.SetInputData(poly_data)
        featureEdges.Update()

        # glyph3D vertex marker
        vtxGlyphs = vtk.vtkGlyph3D()
        vtxGlyphs.SetOutputPointsPrecision(vtk.vtkAlgorithm.DOUBLE_PRECISION)
        # todo: markers sometimes too small or too large
        vtxGlyphs.SetScaleFactor(0.05)
        vtxGlyphs.SetSourceData(self.glyphData)
        vtxGlyphs.SetInputData(poly_data)
        vtxGlyphs.Update()

        vtxMapper = vtk.vtkPolyDataMapper()
        vtxMapper.SetInputConnection(vtxGlyphs.GetOutputPort())

        vtxActor = vtk.vtkActor()
        vtxActor.SetMapper(vtxMapper)
        vtxActor.PickableOff()
        vtxActor.VisibilityOff()
        vtxActor.GetProperty().SetLineWidth(2)
        vtxActor.GetProperty().SetColor(1.0, 0.0, 0.0)

        edgeMapper = vtk.vtkPolyDataMapper()
        edgeMapper.SetInputConnection(featureEdges.GetOutputPort())
        edgeActor = vtk.vtkActor()
        edgeActor.GetProperty().SetLineWidth(3)  # TODO: Width option in GUI?
        edgeActor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Black"))
        edgeActor.SetMapper(edgeMapper)

        poly_mapper.SetInputData(tri_filter.GetOutput())

        # The actor is a grouping mechanism: besides the geometry (mapper), it
        # also has a property, transformation matrix, and/or texture map.
        # Here we set its color and rotate it -22.5 degrees.
        actor = vtk.vtkActor()
        actor.SetMapper(poly_mapper)
        actor.GetProperty().SetColor(colour)
        actor.PickableOff()

        self.vtkActor = actor, edgeActor, vtxActor
        self.pickable_actor = edgeActor
        return [actor, edgeActor, vtxActor]

    def set_highlight(self, highlighted):
        actor, edgeActor, vtxActor = self.vtkActor
        if highlighted:
            actor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Yellow"))
            vtxActor.VisibilityOn()
        else:
            actor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Orange"))
            vtxActor.VisibilityOff()


class VtkPolygonLayer(VtkPolyLayer):
    make_vertexts = Mixin2D.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkPolygonZLayer(VtkPolyLayer):
    make_vertexts = Mixin3D.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkPolygonMLayer(VtkPolyLayer):
    make_vertexts = MixinM.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkPolygonZMLayer(VtkPolyLayer):
    make_vertexts = MixinZM.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkMultiPolygonLayer(VtkPolyLayer):
    make_vertexts = Mixin2D.make_vertexts
    make_wkt = MixinMulti.make_wkt


class VtkMultiPolygonZLayer(VtkPolyLayer):
    make_vertexts = Mixin3D.make_vertexts
    make_wkt = MixinMulti.make_wkt


class VtkMultiPolygonMLayer(MixinM, MixinMulti, VtkPolyLayer):
    make_vertexts = MixinM.make_vertexts
    make_wkt = MixinMulti.make_wkt


class VtkMultiPolygonZMLayer(MixinZM, MixinMulti, VtkPolyLayer):
    make_vertexts = MixinZM.make_vertexts
    make_wkt = MixinMulti.make_wkt


class VtkLineLayer(VtkLayer):
    def get_actors(self, colour):
        poly_data = self.extractor.startExtraction()
        lineMapper = vtk.vtkPolyDataMapper()
        lineMapper.SetInputData(poly_data)
        lineActor = vtk.vtkActor()
        lineActor.SetMapper(lineMapper)
        lineActor.GetProperty().SetColor(colour)
        lineActor.GetProperty().SetLineWidth(3)

        # glyph3D vertex marker
        vtxGlyphs = vtk.vtkGlyph3D()
        vtxGlyphs.SetOutputPointsPrecision(vtk.vtkAlgorithm.DOUBLE_PRECISION)
        # todo: markers sometimes too small or too large
        vtxGlyphs.SetScaleFactor(0.05)
        vtxGlyphs.SetSourceData(self.glyphData)
        vtxGlyphs.SetInputData(poly_data)
        vtxGlyphs.Update()

        vtxMapper = vtk.vtkPolyDataMapper()
        vtxMapper.SetInputConnection(vtxGlyphs.GetOutputPort())

        vtxActor = vtk.vtkActor()
        vtxActor.SetMapper(vtxMapper)
        vtxActor.PickableOff()
        vtxActor.VisibilityOff()
        vtxActor.GetProperty().SetLineWidth(2)
        vtxActor.GetProperty().SetColor(1.0, 0.0, 0.0)

        self.vtkActor = lineActor, vtxActor
        self.pickable_actor = lineActor
        return [lineActor, vtxActor]

    def set_highlight(self, highlighted):
        lineActor, vtxActor = self.vtkActor
        if highlighted:
            vtxActor.VisibilityOn()
            lineActor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Yellow"))
        else:
            vtxActor.VisibilityOff()
            lineActor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Black"))


class VtkLineStringLayer(VtkLineLayer):
    make_vertexts = Mixin2D.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkLineStringZLayer(VtkLineLayer):
    make_vertexts = Mixin3D.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkLineStringMLayer(VtkLineLayer):
    make_vertexts = MixinM.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkLineStringZMLayer(VtkLineLayer):
    make_vertexts = MixinZM.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkMultiLineStringLayer(VtkLineLayer):
    make_vertexts = Mixin2D.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkMultiLineStringZLayer(VtkLineLayer):
    make_vertexts = Mixin3D.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkMultiLineStringMLayer(VtkLineLayer):
    make_vertexts = MixinM.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkMultiLineStringZMLayer(VtkLineLayer):
    make_vertexts = MixinZM.make_vertexts
    make_wkt = MixinSingle.make_wkt


class VtkPointLayer(VtkLayer):
    def make_wkt(self, vertices):
        if ('Z' or 'M') not in self.wkbTypeName[-2:]:
            vertexts = [f'({v[0]} {v[1]})' for v in vertices]
        elif self.wkbTypeName[-2:] == 'ZM':
            vertexts = [f'({v[0]} {v[1]} {v[2]} {0.0})' for v in vertices]
        elif self.wkbTypeName[-1] == 'M':
            vertexts = [f'({v[0]} {v[1]} {0.0})' for v in vertices]
        else:
            vertexts = [f'({v[0]} {v[1]} {v[2]})' for v in vertices]
        if self.isMulti:
            wkt = '{0}({1})'.format(self.wkbTypeName, ', '.join(vertexts))
        else:
            wkt = []
            for v in vertexts:
                wkt.append('{0}{1}'.format(self.wkbTypeName, v))
        return wkt

    def get_actors(self, colour):
        poly_data = self.extractor.startExtraction()
        pointMapper = vtk.vtkPolyDataMapper()
        pointMapper.SetInputData(poly_data)
        pointActor = vtk.vtkActor()
        pointActor.SetMapper(pointMapper)
        pointActor.GetProperty().SetPointSize(5)
        # TODO: Has to be disabled for Intel HD Graphics 4000 - points dont get rendered
        pointActor.GetProperty().RenderPointsAsSpheresOn()
        pointActor.GetProperty().SetColor(colour)

        self.vtkActor = pointActor
        self.pickable_actor = pointActor
        return [pointActor]

    def set_highlight(self, highlighted):
        if highlighted:
            self.vtkActor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Yellow"))
        else:
            self.vtkActor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Orange"))


class VtkWidget(QVTKRenderWindowInteractor):
    layer_type_map = {
        'Polygon': VtkPolygonLayer,
        'PolygonM': VtkPolygonMLayer,
        'PolygonZ': VtkPolygonZLayer,
        'PolygonZM': VtkPolygonZMLayer,
        'MultiPolygon': VtkMultiPolygonLayer,
        'MultiPolygonZ': VtkMultiPolygonZLayer,
        'MultiPolygonM': VtkMultiPolygonMLayer,
        'MultiPolygonZM': VtkMultiPolygonZMLayer,
        'LineString': VtkLineStringLayer,
        'LineStringM': VtkLineStringMLayer,
        'LineStringZ': VtkLineStringZLayer,
        'LineStringZM': VtkLineStringZMLayer,
        'MultiLineString': VtkMultiLineStringLayer,
        'MultiLineStringM': VtkMultiLineStringMLayer,
        'MultiLineStringZ': VtkMultiLineStringZLayer,
        'MultiLineStringZM': VtkMultiLineStringZMLayer,
        'Point': VtkPointLayer,
        'PointM': VtkPointLayer,
        'PointZ': VtkPointLayer,
        'PointZM': VtkPointLayer,
        'MultiPoint': VtkPointLayer,
        'MultiPointM': VtkPointLayer,
        'MultiPointZ': VtkPointLayer,
        'MultiPointZM': VtkPointLayer
    }

    def __init__(self, widget):
        self.renderer = vtk.vtkRenderer()
        self.axes = vtk.vtkAxesActor()
        self.axes.PickableOff()
        self.colour_provider = ColourProvider()
        super().__init__(widget)
        self.GetRenderWindow().AddRenderer(self.renderer)
        self.layers = {}

    def switch_layer(self, qgis_layer):
        layer_id = qgis_layer.id()
        type_name = QgsWkbTypes.displayString(qgis_layer.wkbType())
        if type_name in VtkWidget.layer_type_map.keys():
            if layer_id not in self.layers.keys():
                layer_type = VtkWidget.layer_type_map[type_name]
                created = layer_type(qgs_layer=qgis_layer)
                # created.update()
                self.layers[layer_id] = created
                for actor in created.get_actors(self.colour_provider.next()):
                    self.renderer.AddActor(actor)
                # for actor in created.get_actors(qgis_layer.renderer().symbols(QgsRenderContext())[0].color().getRgb()[0:3]):
                #     self.renderer.AddActor(actor)
        else:
            print(f"No class defined for {type_name}")
        self.refresh_content()

    def refresh_layer(self, layer):
        vtk_layer = self.layers.pop(layer.id())
        actors = vtk_layer.vtkActor
        if type(actors) != tuple:
            tuple(actors)
        for actor in actors:
            self.renderer.RemoveActor(actor)

    def refresh_content(self):
        # The mapper is responsible for pushing the geometry into the graphics
        # library. It may also do color mapping, if scalars or other
        # attributes are defined.

        # Create the graphics structure. The renderer renders into the render
        # window. The render window interactor captures mouse events and will
        # perform appropriate camera or actor manipulation depending on the
        # nature of the events.

        ren = self.renderer
        renWin = self.GetRenderWindow()
        # no visible difference
        # renWin.PointSmoothingOn()  # Point Cloud test
        # renWin.PolygonSmoothingOn()
        # renWin.LineSmoothingOn()
        iren = renWin.GetInteractor()
        iren.SetRenderWindow(renWin)

        # Add the actors to the renderer, set the background and size
        ren.SetBackground(vtk.vtkNamedColors().GetColor3d("light_grey"))

        # This allows the interactor to initalize itself. It has to be
        # called before an event loop.
        iren.Initialize()

        # We'll zoom in a little by accessing the camera and invoking a "Zoom"
        # method on it.
        # ren.ResetCamera()
        # ren.GetActiveCamera().Zoom(1.5)
        renWin.Render()


# call on right button down, to track last point
class PointAdded(QObject):
    signal = pyqtSignal()


class VtkMouseInteractorStyle(vtk.vtkInteractorStyleTrackballCamera):
    def __init__(self, parent=None):
        self.point_added = PointAdded()
        # float = priority (0.0 = lowest)
        self.AddObserver("RightButtonPressEvent", self.right_button_press_event, 1.0)
        # self.AddObserver("MouseMoveEvent", self.mouse_move_event, 0.0)
        # self.AddObserver("RightButtonReleaseEvent", self.right_button_release_event, 1.0)
        # self.AddObserver("MouseWheelForwardEvent", self.mouse_wheel_forward_event, 1.0)
        # self.AddObserver("MouseWheelBackwardEvent", self.mouse_wheel_backward_event, 1.0)
        self.default_color = (0.0, 1.0, 1.0)
        self.select_color = (1.0, 0.2, 0.2)
        self.vertices = []
        self.select_index = -1

        self.initialize_geometry_info()

        self.vertices_actor = vtk.vtkActor()
        self.selected_vertex_actor = vtk.vtkActor()
        self.poly_line_actor = vtk.vtkActor()
        self.actors = [self.vertices_actor,
                       self.selected_vertex_actor,
                       self.poly_line_actor]
        self.last_source = None

    def initialize_geometry_info(self):
        self.vtk_points = vtk.vtkPoints()
        self.vtk_points.SetDataTypeToDouble()
        self.vertex_cell_array = vtk.vtkCellArray()
        self.poly_data = vtk.vtkPolyData()

    # Creates a vtkPoints with RenderAsSpheresOn on a selected point and appends point coordinates to self.vertices
    # TODO: GetCurrentRenderer only works if RenderWindow was interacted with (e.g. zoomed, rotated)
    def OnRightButtonDown(self):
        clickPos = self.GetInteractor().GetEventPosition()
        picker = vtk.vtkPointPicker()
        picker.SetTolerance(100)
        picker.Pick(clickPos[0], clickPos[1], 0, self.GetCurrentRenderer())
        picked = picker.GetPickPosition()
        picked_actor = picker.GetActor()
        print("vtkPointPicker picked: ", picked)
        #picked_actor.GetProperty().SetColor(vtk.vtkNamedColors().GetColor3d("Blue"))

        # return if picked point already in vertices
        if picked in self.vertices:
            return
        # return if selection is not an actor
        if picked_actor is None:
            return
        self.add_vertex(picked, picked_actor)
        self.point_added.signal.emit()

    def add_vertex(self, vertex, source=None):
        self.last_source = source
        self.vertices.append(vertex)
        self.draw()

    def trace(self):
        def deduplicate(seq):
            seen = set()
            seen_add = seen.add
            return [x for x in seq if not (x in seen or seen_add(x))]
        # We get the last three picked vertices
        points = self.vertices[-3:]
        if self.last_source and len(points) == 3:
            features = self.last_source.features
            try:
                feature = features.get_common(points)
            except ValueError as error:
                print(error)
                return
            feature = SimpleRingBuffer(feature)
            trace = feature.slice(*points)
            trace = deduplicate(trace)
            if not trace[0] == points[0]:
                trace.reverse()
            print(f"We have {len(self.vertices)} and replace the last three with {len(trace)} traced ones.")
            self.vertices = self.vertices[:-3] + trace
            self.draw()

    def draw(self):
        for actor in self.actors:
            if actor is None:
                continue
            if self.GetCurrentRenderer() is None:
                continue
            self.GetCurrentRenderer().RemoveActor(actor)
            actor.SetMapper(None)
        # put all self.vertices into vertex_actor
        # build poly_line from vertices
        # add selected point actor (and make it slightly bigger)
        # add all of them to the renderer
        self.initialize_geometry_info()
        for vertex in self.vertices:
            pid = self.vtk_points.InsertNextPoint(vertex)
            self.vertex_cell_array.InsertNextCell(1, [pid])
        self.vtk_points.Modified()
        self.poly_data.SetPoints(self.vtk_points)
        self.poly_data.SetVerts(self.vertex_cell_array)
        pointMapper = vtk.vtkPolyDataMapper()
        pointMapper.SetInputData(self.poly_data)
        self.vertices_actor.SetMapper(pointMapper)
        self.vertices_actor.GetProperty().SetColor(self.default_color)
        self.vertices_actor.GetProperty().SetPointSize(10)
        self.vertices_actor.GetProperty().RenderPointsAsSpheresOn()
        self.vertices_actor.PickableOff()

        # Create polylines from self.vtk_points if there is more than one vertex
        if len(self.vertices):
            polyLine = vtk.vtkPolyLine()
            polyLine.GetPointIds().SetNumberOfIds(len(self.vertices))
            for i in range(len(self.vertices)):
                polyLine.GetPointIds().SetId(i, i)
            cells = vtk.vtkCellArray()
            cells.InsertNextCell(polyLine)

            polyData = vtk.vtkPolyData()
            polyData.SetPoints(self.vtk_points)
            polyData.SetLines(cells)

            lineMapper = vtk.vtkPolyDataMapper()
            lineMapper.SetInputData(polyData)
            self.poly_line_actor.SetMapper(lineMapper)
            self.poly_line_actor.GetProperty().SetColor(1.0, 0.0, 0.0)
            self.poly_line_actor.GetProperty().SetLineWidth(3)
            # self.poly_line_actor.GetProperty().RenderLinesAsTubesOn()  # prevents z-fighting
            self.poly_line_actor.PickableOff()

        # Create bigger, self.select_color selection point
        if self.vertices:
            selected = vtk.vtkPoints()
            selected.SetDataTypeToDouble()
            selected.InsertNextPoint(self.vertices[self.select_index])
            selected_cells = vtk.vtkCellArray()
            selected_cells.InsertNextCell(1, [0])
            selected_poly_data = vtk.vtkPolyData()
            selected_poly_data.SetPoints(selected)
            selected_poly_data.SetVerts(selected_cells)
            selected_mapper = vtk.vtkPolyDataMapper()
            selected_mapper.SetInputData(selected_poly_data)
            self.selected_vertex_actor.SetMapper(selected_mapper)
            self.selected_vertex_actor.GetProperty().SetColor(self.select_color)
            self.selected_vertex_actor.GetProperty().SetPointSize(15)
            self.selected_vertex_actor.GetProperty().RenderPointsAsSpheresOn()
            self.selected_vertex_actor.PickableOff()

        if self.GetCurrentRenderer():
            self.GetCurrentRenderer().AddActor(self.selected_vertex_actor)
            self.GetCurrentRenderer().AddActor(self.vertices_actor)
            self.GetCurrentRenderer().AddActor(self.poly_line_actor)
            self.GetCurrentRenderer().GetRenderWindow().Render()

    def set_selection(self, index):
        # With the changes in draw, this can be used as a receiver for a selection_changed
        # event of a vertex list widget
        self.select_index = index
        self.draw()

    # Remove vertex from self.vertices and self.vtk_points
    def remove_selected(self):
        if self.vertices:
            # working with the select_index allows to work with a wdget that allows selection of vertices.
            del self.vertices[self.select_index]
            self.draw()
            self.point_added.signal.emit()

    def removeAllVertices(self):
        self.vertices = []
        self.draw()

    def OnRightButtonUp(self):
        pass

    def right_button_press_event(self, obj, event):
        # print("Right Button pressed")
        self.OnRightButtonDown()
        return

    def right_button_release_event(self, obj, event):
        print("Right Button released")
        self.OnRightButtonUp()
        return

    # todo: Blocking camera movement? (left click, middle click)
    def OnMouseMove(self):
        clickPos = self.GetInteractor().GetEventPosition()
        # picker = vtk.vtkPointPicker()
        # picker.SetTolerance(0)
        # picked = picker.GetPickPosition()

        # get closest point
        # kdLoc = vtk.vtkKdTreePointLocator()

        # get actor on mouse over - only picks PickableOn()
        picker = vtk.vtkPropPicker()
        picker.Pick(clickPos[0], clickPos[1], 0, self.GetCurrentRenderer())
        picked = picker.GetActor()
        if picked is not None:
            print(picked)
        return

    def mouse_move_event(self, obj, event):
        self.OnMouseMove()
        return

    # todo: glyph3D scaling?
    def OnMouseWheelForward(self):
        pass

    def OnMouseWheelBackward(self):
        pass

    def mouse_wheel_forward_event(self):
        self.OnMouseWheelForward()
        return

    def mouse_wheel_backward_event(self):
        self.OnMouseWheelBackward()
        return
