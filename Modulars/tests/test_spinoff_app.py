def test_regression_placeholder():
    module = __import__('app')
    assert module.__name__ == 'app'
