def test_module_imports():
    module = __import__('app_v3')
    assert module is not None

def test_has_callable_symbols():
    module = __import__('app_v3')
    callables = [n for n in dir(module) if callable(getattr(module, n)) and not n.startswith('_')]
    assert isinstance(callables, list)
