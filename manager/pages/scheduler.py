import dash
import dash_bootstrap_components as b
from dash.html import *

from manager.pages.common.base import layout as BaseLayout

dash.register_page(__name__, path='/')


def layout():
    drag_and_drop_overlay = Div()

    files = [
        b.Stack([
            H5("Files")
        ], direction="horizontal", class_name="gap-2 mb-1"),
    ]

    playlist = [
        b.Stack([
            H5("Playlist")
        ], direction="horizontal", class_name="gap-2"),
    ]

    return BaseLayout([
        # drag_and_drop_overlay,
        b.Row([
            b.Col(files, class_name="col-lg-4 pe-lg-2 order-1 order-lg-12"),
            b.Col(playlist, class_name="col-lg-8 mt-4 mt-lg-0 order-12 order-lg-1"),
        ])
    ])
