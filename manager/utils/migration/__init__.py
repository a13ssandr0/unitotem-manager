def run_advancement():
    from pathlib import Path
    from pkgutil import iter_modules

    #TODO: use natsort
    for module_finder, name, ispkg in iter_modules([str(Path(__file__).parent)]):
        loader = module_finder.find_spec(name).loader
        mod = loader.load_module(name)
        if hasattr(mod, 'advance'):
            mod.advance()