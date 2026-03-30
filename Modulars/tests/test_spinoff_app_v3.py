def test_regression_placeholder():
    module = __import__('app_v3')
    assert module.__name__ == 'app_v3'
