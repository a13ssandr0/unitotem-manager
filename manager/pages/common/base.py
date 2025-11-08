from socket import gethostname
from typing import Sequence

import dash
import dash_bootstrap_components as b
from dash import clientside_callback, callback
from dash.development.base_component import Component
from dash.html import *
from loguru import logger
from utils import constants as const

import resources as R
from pages.utils.icons import Icon, MI


def catch_and_show(func):
    def wrapper():
        try:
            return func()
        except Exception as e:
            logger.exception(e)
            return b.Alert(str(e))

    return wrapper


color_mode_switch = Span(
        [
            MI("dark_mode"),
            b.Switch(id="night-switch", value=True, className="d-inline-block ms-1", persistence=True),
            MI("light_mode"),
        ], className="align-middle ms-auto"
)

clientside_callback(
        """
        (switchOn) => {
           document.documentElement.setAttribute("data-bs-theme", switchOn ? "light" : "dark");
           return window.dash_clientside.no_update
        }
        """,
        dash.Output("night-switch", "id"),
        dash.Input("night-switch", "value"),
)


def get_tabs():
    return [
        ('Playback', 'playback', 'Default asset duration', 'play_arrow', True), #logged_user.has_perm.scheduler),
        ('Audio', 'audio', 'Default output device, Volume, Mute', 'speaker', True), #logged_user.has_perm.audio),
        ('Display', 'display', 'Resolution, Orientation, Window positioning', 'display_settings', False),
        ('Remote control', 'remote', 'Connect to a network of UniTotems', 'graph_2', False),
        ('Security', 'security', 'Password', 'security', False),
        ('Scheduled actions', 'cron', 'Cron-based power scheduler', 'alarm', False),
        ('Network', 'network', 'Hostname, Netplan', 'lan', False),
        ('Updates', 'updates', 'APT', 'update', False),
        ('Backup and restore', 'backup', 'Backup, restore, reset system', 'history', False)
    ]

def menu_select(name, src):
    if src=='/': # (request.url|string).endswith(src)
        return Span(name, className="text-light text-bg-primary fw-bold px-2 py-1 rounded")
    else: return Span(name)


@catch_and_show
def navigation_items():
    tabs = []

    if True: # logged_user.has_perm.scheduler
        tabs.append(b.Row(
                A(
                        [
                            MI("playlist_play", className="text-primary-emphasis me-2"),
                            menu_select('Scheduler', '/')
                        ],
                        href="/", className="text-reset text-decoration-none"
                ),
                className="m-2 ms-0 mb-4"
        ))

    if True: #logged_user.has_perm.admin or logged_user.has_perm.scheduler or logged_user.has_perm.audio
        tabs.append(b.Row(
                A(
                        [
                            MI("settings", className="text-primary-emphasis me-2"),
                            menu_select('Settings', '/settings')
                        ],
                        href="/settings", className="text-reset text-decoration-none"
                ),
                className="m-2 ms-0"
        ))

        for name, tab, _, icon, show in get_tabs():
            if True or show: #logged_user.has_perm.admin or show
                tabs.append(b.Row(
                        A(
                                [
                                    MI(icon, className="text-primary-emphasis me-2"),
                                    menu_select(name, '/settings/'+tab)
                                ],
                                href='/settings/'+tab, className="text-reset text-decoration-none"
                        ),
                        className="m-1 mt-2 ms-3"
                ))

    tabs.append(b.Row(
            A(
                    [
                        MI("bar_chart", className="text-primary-emphasis me-2"),
                        menu_select('Info', '/info')
                    ],
                    href="/info", className="text-reset text-decoration-none"
            ),
            className="m-2 ms-0 mt-4"
    ))


    return tabs


@catch_and_show
def layout(children: str | int | float | Component | None | Sequence[str | int | float | Component | None] = None):
    navbar = b.Navbar(
            b.Container(
                    [
                        b.NavbarToggler(id="navbar-toggler", n_clicks=0),
                        b.NavbarBrand([
                            Span([
                                "U",
                                Span([
                                    "niTotem",
                                    Span([" @ ", gethostname()], className="fs-3"),
                                ], className="d-none d-md-inline")
                            ], className="fw-bold fs-1")
                        ], class_name="p-0"),

                        color_mode_switch,
                        b.Button([MI("replay"), Span("Reboot", className="d-none d-sm-inline ms-1")],
                                 id="btn-reboot", n_clicks=0,
                                 color="light", outline=True),
                        b.Button([MI("power_settings_new"), Span("Shutdown", className="d-none d-sm-inline ms-1")],
                                 id="btn-shutdown", n_clicks=0,
                                 color="light", outline=True),
                        b.Button([MI("logout"), Span("Logout", className="d-none d-sm-inline ms-1")],
                                 id="btn-logout", n_clicks=0,
                                 color="light", outline=True),
                    ],
                    fluid=True, class_name="gap-1 gap-sm-2"
            ),
            color=R.colors.accent,
            class_name="p-0"
    )

    navigation = b.Col(navigation_items(), md=3, className="d-none d-md-block mt-lg-4 mt-1 mb-4")

    offcanvas_nav = b.Offcanvas(
            title=H1("UniTotem", style={"color": R.colors.accent}, className="ms-2"),
            children=navigation_items(),
            id="offcanvas-collapse",
            class_name="d-md-none",
            keyboard=True,
            close_button=False,
            placement="start"
    )

    if not children:
        children = Div(P([
            "Mhhh, looks like there's nothing here.", Br(),
            "Make sure you have the right permissions to see the wonders hidden here..."
        ], className="text-center"), className="d-flex justify-content-center")

    footer = Footer(
            [
                Small([
                    A(
                            [Icon(bi="github"), " Unitotem"],
                            href="https://github.com/a13ssandr0/unitotem", target="_blank", rel="noopener noreferrer",
                    ), f" {const.__version__} by a13ssandr0"
                ]),

                Div(className="vr d-none d-sm-inline-block"),

                Small("Logged in as {user}"),

                Div(className="vr d-none d-sm-inline-block"),

                Small(["Display: ", Span("Not connected", id="display-bounds")]),

                Div(className="vr d-none d-sm-inline-block"),

                Small("Used {disk_used} of {disk_total}"),

                Div(className="vr d-none d-sm-inline-block"),

                Small("Disconnected", id="status_disc", className="text-danger fade-in-out"),
                Small("Connected", id="status_conn", className="text-success d-none"),
            ],
            className="d-flex flex-wrap justify-content-evenly text-muted text-center " ## DO NOT REMOVE THIS SPACE
                      "user-select-none fixed-bottom border-top bg-body w-100 zindex-fixed")

    return Div([
        navbar,
        b.Container(
                [
                    b.Row([
                        navigation,
                        b.Container(children=children, class_name="col mt-lg-4 mt-1 mb-4")
                    ])
                ], fluid=True
        ),
        footer,
        offcanvas_nav
    ], **{'data-bs-theme': "dark"})


# add callback for toggling the collapse on small screens
@callback(
        dash.Output("offcanvas-collapse", "is_open"),
        [dash.Input("navbar-toggler", "n_clicks")],
        [dash.State("offcanvas-collapse", "is_open")],
)
def toggle_offcanvas_collapse(n, is_open):
    if n:
        return not is_open
    return is_open

