"""BlendSVG: cut SVG artwork into a mesh surface as flush inlays for multi-color 3D printing."""


def register() -> None:
    from . import ui

    ui.register()


def unregister() -> None:
    from . import ui

    ui.unregister()
