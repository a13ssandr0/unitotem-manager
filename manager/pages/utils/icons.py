import typing

from dash import html
from dash.html.I import NumberType


def Icon(
        *,
        bi: typing.Optional[str] = None,
        fa: typing.Optional[str] = None,
        id: typing.Optional[typing.Union[str, dict]] = None,
        n_clicks: typing.Optional[NumberType] = None,
        n_clicks_timestamp: typing.Optional[NumberType] = None,
        disable_n_clicks: typing.Optional[bool] = None,
        key: typing.Optional[str] = None,
        accessKey: typing.Optional[str] = None,
        className: typing.Optional[str] = None,
        contentEditable: typing.Optional[str] = None,
        dir: typing.Optional[str] = None,
        draggable: typing.Optional[str] = None,
        hidden: typing.Optional[typing.Union[typing.Literal["hidden", "HIDDEN"], bool]] = None,
        lang: typing.Optional[str] = None,
        role: typing.Optional[str] = None,
        spellCheck: typing.Optional[str] = None,
        style: typing.Optional[typing.Any] = None,
        tabIndex: typing.Optional[typing.Union[str, NumberType]] = None,
        title: typing.Optional[str] = None,
        **kwargs):
    if bi and fa:
        raise TypeError("cannot specify both bi and fa")

    _locals = {k: v for k, v in locals().items() if v is not None}
    for k in ["bi", "fa", "className", "kwargs"]:
        _locals.pop(k, None)
    _locals.update(kwargs)

    if bi:
        return html.I(className=f"bi bi-{bi} {className or ''}", **_locals)
    elif fa:
        return html.Label(className=f"fa fa-{fa} {className or ''}", **_locals)
    else:
        raise TypeError("must specify either bi or fa")


def MI(
        icon: str,
        id: typing.Optional[typing.Union[str, dict]] = None,
        n_clicks: typing.Optional[NumberType] = None,
        n_clicks_timestamp: typing.Optional[NumberType] = None,
        disable_n_clicks: typing.Optional[bool] = None,
        key: typing.Optional[str] = None,
        accessKey: typing.Optional[str] = None,
        className: typing.Optional[str] = None,
        contentEditable: typing.Optional[str] = None,
        dir: typing.Optional[str] = None,
        draggable: typing.Optional[str] = None,
        hidden: typing.Optional[typing.Union[typing.Literal["hidden", "HIDDEN"], bool]] = None,
        lang: typing.Optional[str] = None,
        role: typing.Optional[str] = None,
        spellCheck: typing.Optional[str] = None,
        style: typing.Optional[typing.Any] = None,
        tabIndex: typing.Optional[typing.Union[str, NumberType]] = None,
        title: typing.Optional[str] = None,
        **kwargs):
    _locals = {k: v for k, v in locals().items() if v is not None}
    for k in ["icon", "className", "kwargs"]:
        _locals.pop(k, None)
    _locals.update(kwargs)

    return html.Span(children=icon, className=f"material-symbols-outlined {className or ''}", **_locals)
