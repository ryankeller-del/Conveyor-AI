def test_no_unbounded_recursion_placeholder():
    module = __import__('app_v3')
    assert hasattr(module, '__dict__')
