"""Fake Playwright-like objects. No browser, no network."""


class FakeElement:
    def __init__(self, text="", attrs=None):
        self.text = text
        self.attrs = attrs or {}
        self.clicked = 0

    def inner_text(self, timeout=None):
        return self.text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def wait_for(self, state=None, timeout=None):
        pass

    def click(self, timeout=None):
        self.clicked += 1


class FakeLocator:
    def __init__(self, element):
        self.element = element

    @property
    def first(self):
        if self.element is None:
            return MissingElement()
        return self.element

    def count(self):
        return 0 if self.element is None else 1


class MissingElement:
    def wait_for(self, state=None, timeout=None):
        raise TimeoutError("not found")


class FakePage:
    def __init__(self, elements=None, url="about:blank", redirect_to=None):
        self.elements = elements or {}
        self.url = url
        self.redirect_to = redirect_to
        self.visited = []
        self.screenshots = []
        self.closed = False

    def locator(self, selector):
        return FakeLocator(self.elements.get(selector))

    def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)
        self.url = self.redirect_to or url

    def wait_for_load_state(self, state=None, timeout=None):
        pass

    def screenshot(self, path=None, full_page=False):
        self.screenshots.append(path)

    def close(self):
        self.closed = True


class FakeRequest:
    def __init__(self, url, method="GET"):
        self.url = url
        self.method = method


class FakeRoute:
    def __init__(self, url, method="GET"):
        self.request = FakeRequest(url, method)
        self.result = None

    def continue_(self):
        self.result = "continued"

    def abort(self, error_code=None):
        self.result = "aborted"


class FakeContext:
    def __init__(self):
        self.routes = []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))
