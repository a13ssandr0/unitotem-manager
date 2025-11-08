from manager.pages.common.base import layout as BaseLayout
import dash
import dash_bootstrap_components as b
from dash.html import *

dash.register_page(__name__, path='/')


layout = BaseLayout()