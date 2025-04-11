import inspect
import types
from functools import wraps
from importlib import import_module
from inspect import isclass, isgeneratorfunction, iscoroutinefunction, isasyncgenfunction
from os.path import join

from benedict import benedict
from loguru import logger
from pydantic import validate_call

from api.commons import UPLOADS
from api.ws.permissions import check_permissions
from api.ws.wsmanager import Context
from api.ws.wsmanager import WSManager, WSAPIBase
from utils.objs import dict_sort

REMOTE_WS = WSManager(cache_last=True)
WS = WSManager(cache_last=False)


# noinspection PyUnresolvedReferences
class WebSocketAPI:
    generators: dict[str, types.FunctionType] = {}

    def __init__(self, ws: WSManager, remote_ws: WSManager):
        self.__ws = ws
        self.__remote_ws = remote_ws

    @property
    def tree(self):
        items = benedict({k: (f.__doc__ or '').strip() for k, f in self.generators.items() if '/_' not in k}).unflatten('/')
        return dict_sort(items)

    def import_class(self, path: str, name: str, prefix: str = None):
        logger.debug(f'Importing {name} from {path}')
        cls = import_module(path).__getattribute__(name)
        self.load_class(cls, prefix=prefix)
        logger.info(f'Imported {name} from {path}')

    def load_class(self, cls: type, prefix: str = None):
        if not issubclass(cls, WSAPIBase):
            raise ValueError(f"Class {cls.__name__} is not a subclass of WSAPIBase")

        self.generators.update(self.__treegen(cls, prefix))

    # noinspection PyPep8Naming
    def __treegen(self, Cls: type, prefix: str = None):
        classname = Cls.__name__
        logger.debug("Class: " + classname)
        if prefix is None:
            prefix = classname
        else:
            prefix = join(prefix, classname)

        gen = {}

        cls = Cls(self.__ws, self.__remote_ws)

        for att in dir(cls):
            if not (att.startswith('__') and att.endswith('__')):
                a = cls.__getattribute__(att)
                if callable(a):
                    logger.debug(f"{classname}.{att}: {a}")
                    if isclass(a):
                        gen.update(self.__treegen(a, prefix))
                    else:
                        name = join(prefix, att)
                        validator_kwargs = {'arbitrary_types_allowed': True}
                        try:
                            validator_kwargs.update(a.validator_kwargs)
                        except AttributeError:
                            pass
                        # assign to each function an attribute with its full API path
                        setattr(getattr(Cls, att), 'api_path', name)
                        # transform each function in an async generator
                        gen[name] = self.__make_async_gen(a, validator_kwargs)

        return gen

    @staticmethod
    def __make_async_gen(func, validator_kwargs):
        # noinspection PyArgumentList
        validated_func = validate_call(func, config=validator_kwargs)
        if iscoroutinefunction(func):
            @wraps(validated_func)
            async def async_gen(*args, **kwargs):
                yield await validated_func(*args, **kwargs)
        elif isgeneratorfunction(func):
            @wraps(validated_func)
            async def async_gen(*args, **kwargs):
                for ret in validated_func(*args, **kwargs):
                    yield ret
        elif isasyncgenfunction(func):
            async_gen = validated_func
        else:
            @wraps(validated_func)
            async def async_gen(*args, **kwargs):
                yield validated_func(*args, **kwargs)

        async_gen.__original_func__ = func
        async_gen.__validated_func__ = validated_func
        return async_gen



logger.debug("WebSocketAPI initialization")
api = WebSocketAPI(WS, REMOTE_WS)
logger.info("WebSocketAPI initialized")

api.import_class('api.scheduler', 'Scheduler')
api.import_class('api.scheduler', 'Settings')
api.import_class('api.audio', 'Audio', 'Settings')
api.import_class('api.remote', 'Remote', 'Settings')
api.import_class('api.network', 'Settings')
api.import_class('api.system.cron', 'Cron', 'Settings')
api.import_class('api.system.power', 'Power')
api.import_class('api.system.apt', 'Update', 'Settings')
api.import_class('api.security', 'Security', 'Settings')
api.import_class('api.display', 'Display', 'Settings')


async def handle_call(target, user, request_data):
    func = api.generators[target]
    # noinspection PyTypeChecker
    check_permissions(func, user)

    #Inspect function signature to check if it has a Context parameter
    signature = inspect.signature(func)

    ctx_param = None
    for param_name, param in signature.parameters.items():
        if param.annotation is Context:
            ctx_param = param_name
            break

    if ctx_param:
        request_data[ctx_param] = Context(username=user.name)

    async for ret in func(**request_data):
        yield ret



UPLOADS._callback = lambda x: WS.broadcast('Scheduler/file', files=x)
