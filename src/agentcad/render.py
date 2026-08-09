import math
from pathlib import Path

from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.Aspect import Aspect_DisplayConnection, Aspect_NeutralWindow
from OCP.Graphic3d import (
    Graphic3d_MaterialAspect,
    Graphic3d_NameOfMaterial_Plastered,
    Graphic3d_NameOfMaterial_Silver,
)
from OCP.Image import Image_AlienPixMap
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TCollection import TCollection_AsciiString
from OCP.V3d import (
    V3d_AmbientLight,
    V3d_DirectionalLight,
    V3d_TypeOfOrientation_Zup_AxoRight,
    V3d_TypeOfOrientation_Zup_Back,
    V3d_TypeOfOrientation_Zup_Bottom,
    V3d_TypeOfOrientation_Zup_Front,
    V3d_TypeOfOrientation_Zup_Left,
    V3d_TypeOfOrientation_Zup_Right,
    V3d_TypeOfOrientation_Zup_Top,
    V3d_Viewer,
)
from OCP.gp import gp_Dir

from agentcad.export import _GLB_PALETTE, _parse_color

import ctypes

try:
    libX11 = ctypes.CDLL("libX11.so.6")
    _CMPFUNC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def _x_error_handler(display, error_event):
        return 0
    _x11_error_handler_cb = _CMPFUNC(_x_error_handler)
    libX11.XSetErrorHandler(_x11_error_handler_cb)
except Exception:
    pass


def _render_vtk_batch_fallback(shape, view_specs, output_paths, width=512, height=512):
    import vtk
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    import math

    BRepMesh_IncrementalMesh(shape, 0.1)

    points = vtk.vtkPoints()
    polys = vtk.vtkCellArray()

    # BRep_Tool.Triangulation_s always returns triangles wound for the face's
    # natural (forward) orientation. A face marked TopAbs_REVERSED in the BREP
    # (common after booleans/fillets -- this bracket's own topology has 5 of 16
    # faces reversed) needs its winding flipped too, or its triangles end up
    # with inward-facing normals: fine in OCCT's own renderer (which reads
    # Orientation() when shading) but wrong here, since VTK computes normals
    # purely from winding order. Unflipped, those faces render solid black in
    # any view where they're seen close to head-on.
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    offset = 0
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        reversed_face = face.Orientation() == TopAbs_REVERSED
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                points.InsertNextPoint(p.X(), p.Y(), p.Z())
            for i in range(1, tri.NbTriangles() + 1):
                t = tri.Triangle(i)
                n1, n2, n3 = t.Get()
                if reversed_face:
                    n2, n3 = n3, n2
                polys.InsertNextCell(3)
                polys.InsertCellPoint(offset + n1 - 1)
                polys.InsertCellPoint(offset + n2 - 1)
                polys.InsertCellPoint(offset + n3 - 1)
            offset += tri.NbNodes()
        explorer.Next()

    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(polys)

    # No normals array is attached above, and this polydata's mapper has
    # nothing else to derive lighting from except VTK's on-the-fly
    # screen-space normal estimation -- which is numerically degenerate for
    # any flat face that lands exactly perpendicular to the camera (zero
    # screen-space depth gradient across the face). That's precisely an
    # axis-aligned view (front/top/etc.) of a flat solid, and it renders as
    # solid black. Computing real analytic normals up front avoids relying on
    # that estimation at all. ConsistencyOn()/AutoOrientNormalsOn() also
    # correct any faces whose triangle winding doesn't match their BREP
    # Orientation() (this shape's own topology has 5 of 16 faces reversed).
    normals_filter = vtk.vtkPolyDataNormals()
    normals_filter.SetInputData(polydata)
    normals_filter.ComputePointNormalsOn()
    normals_filter.ComputeCellNormalsOn()
    normals_filter.ConsistencyOn()
    normals_filter.AutoOrientNormalsOn()
    normals_filter.SplittingOff()
    normals_filter.Update()
    polydata = normals_filter.GetOutput()

    bounds = [0]*6
    polydata.GetBounds(bounds)
    cx = (bounds[0] + bounds[1])/2
    cy = (bounds[2] + bounds[3])/2
    cz = (bounds[4] + bounds[5])/2
    dist = max(bounds[1]-bounds[0], bounds[3]-bounds[2], bounds[5]-bounds[4]) * 1.5
    if dist < 1e-6:
        dist = 10.0

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(polydata)

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.8, 0.8, 0.8)
    # A pure directional/positional vtkLight can leave a face at exactly
    # zero illumination if its normal happens to be perpendicular (or worse,
    # opposed) to every light in the rig -- exactly what happened above with
    # a degenerate zero-length "ambient" light direction. Rather than chase
    # light-vector geometry until every possible face orientation is covered,
    # give the material itself a guaranteed brightness floor: no face can
    # render pure black regardless of light placement. (OCCT's own renderer
    # gets the equivalent of this for free from its V3d_AmbientLight, which
    # is a true non-directional ambient term with no "wrong angle" case.)
    actor.GetProperty().SetAmbient(0.35)
    actor.GetProperty().SetDiffuse(0.65)

    renderer = vtk.vtkRenderer()
    renderer.AddActor(actor)
    renderer.SetBackground(0.96, 0.96, 0.96) # match (245,245,245) or close to it

    # No explicit light was ever added here -- this relied entirely on VTK's
    # automatic default headlight (vtkRenderer.AutomaticLightCreation, on by
    # default), which is only created/repositioned relative to the CURRENT
    # camera at Render() time. Mirror the OCCT path's own fixed-in-world-space
    # key+fill lights (see _setup_render above) instead, so lighting doesn't
    # depend on happening to line up with the camera for each of the 7 views
    # rendered from this one reused renderer. The ambient contribution above
    # (actor.Property.Ambient) replaces OCCT's V3d_AmbientLight -- a real
    # vtkLight can't represent non-directional ambient the same way.
    renderer.SetAutomaticLightCreation(False)
    key_light = vtk.vtkLight()
    key_light.SetLightTypeToSceneLight()
    key_light.SetPositional(False)
    key_light.SetPosition(1, -1, -1)
    key_light.SetFocalPoint(0, 0, 0)
    key_light.SetIntensity(0.8)
    renderer.AddLight(key_light)
    fill_light = vtk.vtkLight()
    fill_light.SetLightTypeToSceneLight()
    fill_light.SetPositional(False)
    fill_light.SetPosition(-1, 1, 0.5)
    fill_light.SetFocalPoint(0, 0, 0)
    fill_light.SetIntensity(0.4)
    renderer.AddLight(fill_light)

    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(width, height)
    window.AddRenderer(renderer)
    
    VIEWS = {
        "front": (0, -1, 0),
        "back": (0, 1, 0),
        "left": (-1, 0, 0),
        "right": (1, 0, 0),
        "top": (0, 0, 1),
        "bottom": (0, 0, -1),
        "iso": (1, -1, 1),
    }

    for spec, out_path in zip(view_specs, output_paths):
        camera = renderer.GetActiveCamera()
        camera.SetFocalPoint(cx, cy, cz)
        
        if isinstance(spec, str):
            dx, dy, dz = VIEWS.get(spec, (1, -1, 1))
        else:
            az, el = spec
            az_r = math.radians(az)
            el_r = math.radians(el)
            dx = -math.sin(az_r) * math.cos(el_r)
            dy = math.cos(az_r) * math.cos(el_r)
            dz = -math.sin(el_r)
        
        l = math.hypot(dx, dy, dz)
        if l > 0: dx, dy, dz = dx/l, dy/l, dz/l
        
        camera.SetPosition(cx + dx*dist, cy + dy*dist, cz + dz*dist)
        
        if abs(dz) > 0.99:
            camera.SetViewUp(0, 1, 0)
        else:
            camera.SetViewUp(0, 0, 1)
            
        renderer.ResetCamera()
        window.Render()
        
        window_filter = vtk.vtkWindowToImageFilter()
        window_filter.SetInput(window)
        window_filter.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetFileName(str(out_path))
        writer.SetInputConnection(window_filter.GetOutputPort())
        writer.Write()

VIEWS = {
    "front": V3d_TypeOfOrientation_Zup_Front,
    "back": V3d_TypeOfOrientation_Zup_Back,
    "left": V3d_TypeOfOrientation_Zup_Left,
    "right": V3d_TypeOfOrientation_Zup_Right,
    "top": V3d_TypeOfOrientation_Zup_Top,
    "bottom": V3d_TypeOfOrientation_Zup_Bottom,
    "iso": V3d_TypeOfOrientation_Zup_AxoRight,
}

ALL_VIEWS = ["front", "right", "top", "iso"]

NAMED_VIEWS = set(VIEWS.keys())


def parse_view_spec(spec):
    """Parse a --view spec string into a list of (type, value) tuples.

    Returns:
        List of ("named", view_name) or ("custom", (azimuth, elevation)) tuples.

    Raises:
        ValueError: If the spec is invalid.
    """
    parts = [p.strip() for p in spec.split(",")]

    # "all" shorthand
    if parts == ["all"]:
        return [("named", v) for v in ALL_VIEWS]

    # All named views (fast path, backward compat)
    if all(p in NAMED_VIEWS for p in parts):
        return [("named", p) for p in parts]

    # Check for colon-separated angles (mixed mode)
    if any(":" in p for p in parts):
        result = []
        for p in parts:
            if p in NAMED_VIEWS:
                result.append(("named", p))
            elif ":" in p:
                az_s, el_s = p.split(":", 1)
                try:
                    result.append(("custom", (float(az_s), float(el_s))))
                except ValueError:
                    raise ValueError(f"Invalid angle spec '{p}'. Use 'azimuth:elevation'.")
            else:
                raise ValueError(
                    f"Invalid view '{p}' in spec '{spec}'. "
                    f"Named views: {', '.join(sorted(NAMED_VIEWS))}. "
                    f"Custom angles: 'azimuth:elevation'."
                )
        return result

    # Legacy: exactly 2 numeric parts → single custom angle
    if len(parts) == 2:
        try:
            return [("custom", (float(parts[0]), float(parts[1])))]
        except ValueError:
            pass

    raise ValueError(
        f"Invalid view spec '{spec}'. Use named views "
        f"({', '.join(sorted(NAMED_VIEWS))}), 'all', or 'azimuth:elevation'."
    )


def _setup_render(shape, width=800, height=600, parts=None, msaa=0):
    """Set up offscreen rendering pipeline, returning (view, context)."""
    display_connection = Aspect_DisplayConnection()
    driver = OpenGl_GraphicDriver(display_connection)
    driver.ChangeOptions().contextNoAccel = True
    driver.ChangeOptions().buffersNoSwap = True

    viewer = V3d_Viewer(driver)
    ambient = V3d_AmbientLight(Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB))
    viewer.AddLight(ambient)
    viewer.SetLightOn(ambient)
    key_light = V3d_DirectionalLight(
        gp_Dir(1, -1, -1),
        Quantity_Color(0.8, 0.8, 0.8, Quantity_TOC_RGB),
    )
    viewer.AddLight(key_light)
    viewer.SetLightOn(key_light)
    fill_light = V3d_DirectionalLight(
        gp_Dir(-1, 1, 0.5),
        Quantity_Color(0.4, 0.4, 0.4, Quantity_TOC_RGB),
    )
    viewer.AddLight(fill_light)
    viewer.SetLightOn(fill_light)

    view = viewer.CreateView()
    view.ChangeRenderingParams().NbMsaaSamples = msaa
    window = Aspect_NeutralWindow()
    window.SetSize(width, height)
    view.SetWindow(window)

    context = AIS_InteractiveContext(viewer)

    part_items = []
    for idx, part in enumerate(parts or []):
        part_shape = part.get("topo_shape")
        if part_shape is None:
            continue
        part_items.append((
            part_shape,
            _parse_color(part.get("color")),
            idx,
            part.get("material"),
        ))

    if part_items:
        for part_shape, parsed_color, idx, material in part_items:
            ais_shape = AIS_Shape(part_shape)
            material_name = (
                Graphic3d_NameOfMaterial_Plastered
                if material == "matte"
                else Graphic3d_NameOfMaterial_Silver
            )
            ais_shape.SetMaterial(Graphic3d_MaterialAspect(material_name))
            r, g, b = parsed_color or _GLB_PALETTE[idx % len(_GLB_PALETTE)]
            ais_shape.SetColor(Quantity_Color(r, g, b, Quantity_TOC_RGB))
            context.Display(ais_shape, 1, -1, False)
        context.UpdateCurrentViewer()
    else:
        ais_shape = AIS_Shape(shape)
        ais_shape.SetMaterial(Graphic3d_MaterialAspect(Graphic3d_NameOfMaterial_Silver))
        context.Display(ais_shape, 1, -1, True)

    return view, context


def _capture(view, output_path, width=800, height=600, msaa=0):
    """Capture the current view to a PNG file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Some offscreen/software-GL drivers accept NbMsaaSamples but do not apply
    # it to V3d_View.ToPixMap(). Always render nonzero-MSAA requests at 2x
    # resolution and downsample so antialiasing is effective on those drivers.
    capture_scale = 2 if msaa else 1
    pixmap = Image_AlienPixMap()
    view.ToPixMap(pixmap, width * capture_scale, height * capture_scale)
    pixmap.Save(TCollection_AsciiString(str(output_path)))
    if capture_scale > 1:
        from PIL import Image

        with Image.open(output_path) as image:
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
            resized.save(output_path)


def _apply_camera(view, zoom, focus, fit):
    """Apply focus, fit, and zoom to a view."""
    if focus:
        view.SetAt(focus[0], focus[1], focus[2])
    if fit:
        view.FitAll()
    if zoom != 1.0:
        view.SetZoom(zoom)
    view.Redraw()


def render_shape(shape, view_name, output_path, width=800, height=600,
                 zoom=1.0, focus=None, fit=True, parts=None, msaa=0):
    """Render a TopoDS_Shape to a PNG file from the given view."""
    try:
        orientation = VIEWS[view_name]
        view, _ctx = _setup_render(shape, width, height, parts=parts, msaa=msaa)

        view.SetProj(orientation)
        _apply_camera(view, zoom, focus, fit)

        _capture(view, output_path, width, height, msaa=msaa)
    except Exception:
        _render_vtk_batch_fallback(shape, [view_name], [output_path], width=width, height=height)


def render_shape_batch(shape, view_specs, output_paths, width=512, height=512,
                       parts=None, msaa=0):
    """Render multiple views of the same shape through ONE viewer setup.

    Shares the OpenGL context, lights, and AIS geometry upload across all views —
    much faster than N sequential render_shape() calls that each build fresh
    setup from scratch.

    Args:
        shape: TopoDS_Shape to render.
        view_specs: Iterable of either named views ("front", "iso", ...) or
            custom (azimuth, elevation) tuples in degrees.
        output_paths: Parallel iterable of output PNG paths.
        width, height: Per-view dimensions in pixels.
        msaa: Number of multisample antialiasing samples; 0 disables MSAA.
    """
    try:
        view, _ctx = _setup_render(shape, width, height, parts=parts, msaa=msaa)
        for spec, out_path in zip(view_specs, output_paths):
            if isinstance(spec, str):
                view.SetProj(VIEWS[spec])
            else:
                az, el = spec
                az_r = math.radians(az)
                el_r = math.radians(el)
                vx = -math.sin(az_r) * math.cos(el_r)
                vy = math.cos(az_r) * math.cos(el_r)
                vz = -math.sin(el_r)
                view.SetProj(vx, vy, vz)
                view.SetUp(0, 0, 1)
            view.FitAll()
            view.Redraw()
            _capture(view, out_path, width, height, msaa=msaa)
    except Exception:
        _render_vtk_batch_fallback(shape, view_specs, output_paths, width=width, height=height)


# The default composite is one top-down layout view + three iso angles spaced
# around the part, so any asymmetric feature (arm, clamp, bracket, etc.) is
# visible from at least one angle. Chosen over the classic orthographic
# front/right/top/iso set because, for agents, dimensions come from the
# metrics JSON — images need to maximize geometry coverage, not ruler-accuracy.
_COMPOSITE_VIEWS = [
    ("TOP", "top"),
    ("ISO FRONT-RIGHT", (45, 25)),
    ("ISO BACK-RIGHT", (-45, 25)),
    ("ISO BACK-LEFT", (-135, 25)),
]


def _image_label_font(size):
    from PIL import ImageFont

    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _projected_shape_extent(shape, view_spec):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bbox = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    dimensions = (xmax - xmin, ymax - ymin, zmax - zmin)

    if view_spec == "top":
        return max(dimensions[0], dimensions[1])

    azimuth, elevation = view_spec
    az_r = math.radians(azimuth)
    el_r = math.radians(elevation)
    direction = (
        -math.sin(az_r) * math.cos(el_r),
        math.cos(az_r) * math.cos(el_r),
        -math.sin(el_r),
    )
    right = (direction[1], -direction[0], 0.0)
    right_length = math.hypot(right[0], right[1])
    right = (right[0] / right_length, right[1] / right_length, 0.0)
    up = (
        right[1] * direction[2],
        -right[0] * direction[2],
        right[0] * direction[1] - right[1] * direction[0],
    )

    half = tuple(value / 2 for value in dimensions)
    corners = [
        (x, y, z)
        for x in (-half[0], half[0])
        for y in (-half[1], half[1])
        for z in (-half[2], half[2])
    ]
    horizontal = [sum(point[i] * right[i] for i in range(3)) for point in corners]
    vertical = [sum(point[i] * up[i] for i in range(3)) for point in corners]
    return max(max(horizontal) - min(horizontal), max(vertical) - min(vertical))


def _comparison_frame_scales(shape_a, shape_b, view_spec):
    extent_a = _projected_shape_extent(shape_a, view_spec)
    extent_b = _projected_shape_extent(shape_b, view_spec)
    common_extent = max(extent_a, extent_b)
    if common_extent <= 0:
        return 1.0, 1.0
    return extent_a / common_extent, extent_b / common_extent


def _scale_image_about_center(image, scale):
    from PIL import Image

    if scale >= 0.999:
        return image
    scaled_size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    resized = image.resize(scaled_size, Image.Resampling.LANCZOS)
    framed = Image.new("RGB", image.size, image.getpixel((0, 0)))
    framed.paste(
        resized,
        ((image.width - resized.width) // 2, (image.height - resized.height) // 2),
    )
    return framed


def _object_mask(image, threshold=8):
    from PIL import Image, ImageChops

    corners = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]
    background = tuple(
        round(sum(pixel[channel] for pixel in corners) / len(corners))
        for channel in range(3)
    )
    difference = ImageChops.difference(
        image, Image.new("RGB", image.size, background)
    ).convert("L")
    return difference.point(lambda value: 255 if value > threshold else 0)


def _edge_mask(image, object_mask):
    from PIL import ImageChops, ImageFilter

    boundary = ImageChops.subtract(
        object_mask.filter(ImageFilter.MaxFilter(5)),
        object_mask.filter(ImageFilter.MinFilter(5)),
    )
    gradients = (
        image.convert("L")
        .filter(ImageFilter.GaussianBlur(radius=0.6))
        .filter(ImageFilter.FIND_EDGES)
        .point(lambda value: 255 if value > 18 else 0)
    )
    surface_edges = ImageChops.multiply(
        gradients, object_mask.filter(ImageFilter.MaxFilter(3))
    )
    return ImageChops.lighter(boundary, surface_edges)


def _mask_pixel_count(mask):
    return mask.histogram()[255]


def _semantic_diff_panel(image_a, image_b):
    from PIL import Image, ImageChops

    mask_a = _object_mask(image_a)
    mask_b = _object_mask(image_b)
    shared = ImageChops.multiply(mask_a, mask_b)
    removed = ImageChops.subtract(mask_a, mask_b)
    added = ImageChops.subtract(mask_b, mask_a)
    union = ImageChops.lighter(mask_a, mask_b)

    edge_a = _edge_mask(image_a, mask_a)
    edge_b = _edge_mask(image_b, mask_b)
    shared_edges = ImageChops.multiply(edge_a, edge_b)
    removed_edges = ImageChops.subtract(edge_a, edge_b)
    added_edges = ImageChops.subtract(edge_b, edge_a)

    panel = Image.new("RGB", image_a.size, (255, 255, 255))
    panel.paste((210, 214, 220), mask=shared)
    panel.paste((0, 114, 178), mask=removed)
    panel.paste((230, 159, 0), mask=added)
    panel.paste((0, 62, 105), mask=removed_edges)
    panel.paste((145, 82, 0), mask=added_edges)
    panel.paste((55, 65, 81), mask=shared_edges)

    union_pixels = _mask_pixel_count(union)
    shared_pixels = _mask_pixel_count(shared)
    removed_pixels = _mask_pixel_count(removed)
    added_pixels = _mask_pixel_count(added)
    denominator = union_pixels or 1
    return panel, {
        "coincident_fraction_of_union": round(
            shared_pixels / denominator,
            4,
        ),
        "reference_only_fraction_of_union": round(
            removed_pixels / denominator,
            4,
        ),
        "candidate_only_fraction_of_union": round(
            added_pixels / denominator,
            4,
        ),
        "_shared_pixels": shared_pixels,
        "_union_pixels": union_pixels,
    }


def _overlap_classification(ratio):
    if ratio >= 0.8:
        return "high"
    if ratio >= 0.5:
        return "moderate"
    return "low"


def render_composite_4view(shape, output_path, per_view_size=512, parts=None):
    """Render a 4-panel composite: top view + three iso angles spaced around the part.

    This is the default preview agents get after every successful run. One
    shape → four informative angles → single image.
    """
    import tempfile
    from PIL import Image, ImageDraw

    labels = [v[0] for v in _COMPOSITE_VIEWS]
    specs = [v[1] for v in _COMPOSITE_VIEWS]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_paths = [Path(tmp) / f"panel_{i}.png" for i in range(len(specs))]
        render_shape_batch(shape, specs, tmp_paths,
                           width=per_view_size, height=per_view_size, parts=parts)

        imgs = [Image.open(p).convert("RGB") for p in tmp_paths]
        w, h = per_view_size, per_view_size
        label_h = 30
        label_font = _image_label_font(18)
        composite = Image.new("RGB", (w * 2, (h + label_h) * 2), (245, 245, 245))

        # 2x2 grid in row-major order
        positions = [(0, 0), (w, 0), (0, h + label_h), (w, h + label_h)]
        for img, (x, y) in zip(imgs, positions):
            composite.paste(img, (x, y + label_h))

        draw = ImageDraw.Draw(composite)
        for (x, y), label in zip(positions, labels):
            draw.text((x + 10, y + 4), label, fill=(40, 40, 40), font=label_font)
        # Dividers
        draw.line([(w, 0), (w, (h + label_h) * 2)], fill=(200, 200, 200), width=1)
        draw.line([(0, h + label_h), (w * 2, h + label_h)], fill=(200, 200, 200), width=1)

        composite.save(str(output_path))


def render_solid_comparison(
    shape,
    parts,
    output_path,
    per_view_size=512,
    comparison_data=None,
):
    """Render actual shared/reference-only/candidate-only 3D volumes."""
    import tempfile
    from PIL import Image, ImageDraw

    legend_h = 132
    legend_font = _image_label_font(18)
    with tempfile.TemporaryDirectory() as tmp:
        composite_path = Path(tmp) / "solid_comparison.png"
        render_composite_4view(
            shape,
            composite_path,
            per_view_size=per_view_size,
            parts=parts,
        )
        with Image.open(composite_path) as source:
            source = source.convert("RGB")
            output = Image.new(
                "RGB",
                (source.width, source.height + legend_h),
                (245, 245, 245),
            )
            output.paste(source, (0, legend_h))

        draw = ImageDraw.Draw(output)
        draw.text(
            (10, 8),
            "SOURCE-FRAME 3D VOLUME | NO ALIGNMENT APPLIED",
            fill=(40, 40, 40),
            font=legend_font,
        )
        volumes = (comparison_data or {}).get("volumes", {})
        unit = (comparison_data or {}).get("units", {}).get("volume")

        def _legend_text(label, key):
            if key not in volumes or not unit:
                return label
            return f"{label} | {volumes[key]:,.4f} {unit}"

        legend = (
            (
                (210, 214, 220),
                _legend_text("SHARED 3D VOLUME", "shared"),
            ),
            (
                (0, 114, 178),
                _legend_text("REFERENCE-ONLY 3D VOLUME", "reference_only"),
            ),
            (
                (230, 159, 0),
                _legend_text("CANDIDATE-ONLY 3D VOLUME", "candidate_only"),
            ),
        )
        for index, (color, label) in enumerate(legend):
            y = 40 + index * 30
            draw.rectangle([(10, y + 1), (29, y + 20)], fill=color)
            draw.text((38, y), label, fill=(40, 40, 40), font=legend_font)

        output.save(str(output_path))


def render_diff_overlay(shape_a, shape_b, label_a, label_b, output_path,
                        width=1024, height=1024, view_name="iso",
                        parts_a=None, parts_b=None):
    """Render four center-aligned semantic difference maps.

    Coincident projection is gray, A-only projected pixels are blue, and
    B-only projected pixels are orange. The return value describes the same
    masks used to make the image.
    """
    import tempfile
    from PIL import Image, ImageDraw

    del view_name  # Diff artifacts always use the standard four comparison views.
    labels = [view[0] for view in _COMPOSITE_VIEWS]
    specs = [view[1] for view in _COMPOSITE_VIEWS]
    per_view_size = max(64, min(width, height) // 2)
    label_h = 34
    legend_h = 108
    label_font = _image_label_font(18)
    legend_font = _image_label_font(18)

    with tempfile.TemporaryDirectory() as tmp:
        a_paths = [Path(tmp) / f"a_{i}.png" for i in range(len(specs))]
        b_paths = [Path(tmp) / f"b_{i}.png" for i in range(len(specs))]
        render_shape_batch(
            shape_a,
            specs,
            a_paths,
            width=per_view_size,
            height=per_view_size,
            parts=parts_a,
        )
        render_shape_batch(
            shape_b,
            specs,
            b_paths,
            width=per_view_size,
            height=per_view_size,
            parts=parts_b,
        )

        panels = []
        view_stats = []
        for label, a_path, b_path, spec in zip(labels, a_paths, b_paths, specs):
            a_img = Image.open(a_path).convert("RGB")
            b_img = Image.open(b_path).convert("RGB")
            scale_a, scale_b = _comparison_frame_scales(shape_a, shape_b, spec)
            a_img = _scale_image_about_center(a_img, scale_a)
            b_img = _scale_image_about_center(b_img, scale_b)
            panel, stats = _semantic_diff_panel(a_img, b_img)
            panels.append(panel)
            view_stats.append({
                "view": label.lower().replace(" ", "_").replace("-", "_"),
                **stats,
            })

        panel_stride = per_view_size + label_h
        composite = Image.new(
            "RGB",
            (per_view_size * 2, legend_h + panel_stride * 2),
            (245, 245, 245),
        )
        positions = [
            (0, legend_h),
            (per_view_size, legend_h),
            (0, legend_h + panel_stride),
            (per_view_size, legend_h + panel_stride),
        ]
        for panel, (x, y) in zip(panels, positions):
            composite.paste(panel, (x, y + label_h))

        draw = ImageDraw.Draw(composite)
        draw.rectangle([(10, 9), (29, 28)], fill=(210, 214, 220))
        draw.text(
            (38, 8),
            "COINCIDENT PROJECTED PIXELS",
            fill=(40, 40, 40),
            font=legend_font,
        )
        draw.rectangle([(10, 41), (29, 60)], fill=(0, 114, 178))
        draw.text(
            (38, 40),
            f"REFERENCE-ONLY PROJECTION | A (previous) | {label_a}",
            fill=(40, 40, 40),
            font=legend_font,
        )
        draw.rectangle([(10, 73), (29, 92)], fill=(230, 159, 0))
        draw.text(
            (38, 72),
            f"CANDIDATE-ONLY PROJECTION | B (current) | {label_b}",
            fill=(40, 40, 40),
            font=legend_font,
        )
        for (x, y), label, stats in zip(positions, labels, view_stats):
            overlap = round(stats["coincident_fraction_of_union"] * 100)
            draw.text(
                (x + 10, y + 5),
                f"{label} | OVERLAP {overlap}%",
                fill=(40, 40, 40),
                font=label_font,
            )
        divider_x = per_view_size
        divider_y = legend_h + panel_stride
        draw.line(
            [(divider_x, legend_h), (divider_x, composite.height)],
            fill=(200, 200, 200),
            width=1,
        )
        draw.line(
            [(0, divider_y), (composite.width, divider_y)],
            fill=(200, 200, 200),
            width=1,
        )

        composite.save(str(output_path))

    shared_pixels = sum(stats.pop("_shared_pixels") for stats in view_stats)
    union_pixels = sum(stats.pop("_union_pixels") for stats in view_stats)
    overlap_ratio = round(shared_pixels / (union_pixels or 1), 4)
    return {
        "method": "four_view_image_mask",
        "scope": "2d_projected_pixels",
        "alignment": {
            "mode": "bounding_box_center",
            "rotation": "preserved",
            "relative_scale": "preserved",
        },
        "score": {
            "metric": "projection_intersection_over_union",
            "value": overlap_ratio,
            "classification": _overlap_classification(overlap_ratio),
            "aggregation": "foreground_union_pixel_weighted_across_views",
        },
        "limitations": [
            "Does not establish shared 3D geometry.",
            "Does not identify physical material additions or removals.",
        ],
        "views": view_stats,
    }


def render_diff_side_by_side(shape_a, shape_b, label_a, label_b, output_path,
                             width=512, height=512, view_name="iso",
                             parts_a=None, parts_b=None):
    """Render standard four-view composites for A and B side-by-side."""
    import tempfile
    from PIL import Image, ImageDraw

    del view_name  # Diff artifacts always use the standard four comparison views.
    label_h = 36
    label_font = _image_label_font(20)
    per_view_size = max(64, min(width, height))

    with tempfile.TemporaryDirectory() as tmp:
        a_path = Path(tmp) / "a.png"
        b_path = Path(tmp) / "b.png"
        render_composite_4view(
            shape_a,
            a_path,
            per_view_size=per_view_size,
            parts=parts_a,
        )
        render_composite_4view(
            shape_b,
            b_path,
            per_view_size=per_view_size,
            parts=parts_b,
        )

        a_img = Image.open(a_path).convert("RGB")
        b_img = Image.open(b_path).convert("RGB")

        composite_w = a_img.width + b_img.width
        composite_h = max(a_img.height, b_img.height) + label_h
        composite = Image.new("RGB", (composite_w, composite_h), (245, 245, 245))
        composite.paste(a_img, (0, label_h))
        composite.paste(b_img, (a_img.width, label_h))

        draw = ImageDraw.Draw(composite)
        draw.text(
            (12, 5), f"A (previous) · {label_a}", fill=(40, 40, 40), font=label_font
        )
        draw.text(
            (a_img.width + 12, 5),
            f"B (current) · {label_b}",
            fill=(40, 40, 40),
            font=label_font,
        )
        divider_x = a_img.width
        draw.line([(divider_x, 0), (divider_x, composite_h)], fill=(180, 180, 180), width=1)

        composite.save(str(output_path))


def render_shape_custom(shape, azimuth, elevation, output_path,
                        width=800, height=600, zoom=1.0, focus=None, fit=True,
                        parts=None, msaa=0):
    """Render a TopoDS_Shape to a PNG file from a custom azimuth/elevation angle."""
    try:
        az = math.radians(azimuth)
        el = math.radians(elevation)

        vx = -math.sin(az) * math.cos(el)
        vy = math.cos(az) * math.cos(el)
        vz = -math.sin(el)

        view, _ctx = _setup_render(shape, width, height, parts=parts, msaa=msaa)

        view.SetProj(vx, vy, vz)
        view.SetUp(0, 0, 1)
        _apply_camera(view, zoom, focus, fit)

        _capture(view, output_path, width, height, msaa=msaa)
    except Exception:
        _render_vtk_batch_fallback(shape, [(azimuth, elevation)], [output_path], width=width, height=height)


def render_views(shape, view_names, output_dir):
    """Render multiple views of a shape, returning a dict of {view_name: path_str}."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    for name in view_names:
        out_path = output_dir / f"{name}.png"
        render_shape(shape, name, out_path)
        result[name] = str(out_path)
    return result
