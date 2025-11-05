import dash
from socket import gethostname

from dash import clientside_callback
from dash.html import *
import dash_bootstrap_components as b
import resources as R

app = dash.Dash(
        external_stylesheets=[b.themes.BOOTSTRAP, b.icons.BOOTSTRAP, b.icons.FONT_AWESOME],
)


def layout():
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
                            ], className="my-auto fw-bold fs-1")
                        ]),

                        Span(
                                [
                                    b.Label(className="fa fa-moon", html_for="night-switch"),
                                    b.Switch(id="night-switch", value=True, className="d-inline-block ms-1",
                                             persistence=True),
                                    b.Label(className="fa fa-sun", html_for="night-switch"),
                                ], className=" ms-auto"
                        ),
                        b.Button([I(className="bi bi-bootstrap-reboot me-1"), "Reboot"], id="btn-reboot", n_clicks=0,
                                 color="light", outline=True),  # class_name="ms-auto"),
                        b.Button([I(className="bi bi-power me-1"), "Shutdown"], id="btn-shutdown", n_clicks=0,
                                 color="light", outline=True),
                        b.Button([I(className="bi bi-door-open me-1"), "Logout"], id="btn-logout", n_clicks=0,
                                 color="light", outline=True),
                    ],
                    fluid=True, class_name="gap-2"
            ),
            color=R.colors.accent,
            class_name="p-0"
    )

    return Div([
        navbar,
        # b.Collapse(
        #         search_bar,
        #         id="navbar-collapse",
        #         is_open=True,
        #         navbar=False,
        # ),
    ], **{'data-bs-theme': "dark"})


# add callback for toggling the collapse on small screens
# @app.callback(
#         dash.Output("navbar-collapse", "is_open"),
#         [dash.Input("navbar-toggler", "n_clicks")],
#         [dash.State("navbar-collapse", "is_open")],
# )
# def toggle_navbar_collapse(n, is_open):
#     if n:
#         return not is_open
#     return is_open

clientside_callback(
    """
    (switchOn) => {
       document.documentElement.setAttribute("data-bs-theme", switchOn ? "light" : "dark");
       return window.dash_clientside.no_update
    }
    """,
    dash. Output("night-switch", "id"),
    dash. Input("night-switch", "value"),
)

def _layout():
    try:
        return layout()
    except Exception as e:
        print(e)
        return b.Alert(str(e))


app.layout = _layout

if __name__ == "__main__":
    app.run(debug=True)
