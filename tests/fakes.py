"""Fake Playwright-like objects. No browser, no network."""


class FakeElement:
    def __init__(self, text="", attrs=None, clickable_ancestor=None):
        self.text = text
        self.attrs = attrs or {}
        self.clickable_ancestor = clickable_ancestor
        self.clicked = 0

    def element_handle(self, timeout=None):
        return self

    def query_selector(self, selector):
        assert selector.startswith("xpath=ancestor::")
        return self.clickable_ancestor

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

    def all_inner_texts(self):
        return [] if self.element is None else [self.element.text]


class MissingElement:
    def wait_for(self, state=None, timeout=None):
        raise TimeoutError("not found")


class FakeMouse:
    def __init__(self):
        self.wheel_calls = 0

    def wheel(self, dx, dy):
        self.wheel_calls += 1


class FakePage:
    def __init__(self, elements=None, url="about:blank", redirect_to=None):
        self.elements = elements or {}
        self.url = url
        self.redirect_to = redirect_to
        self.visited = []
        self.screenshots = []
        self.closed = False
        self.handlers = {}
        self.mouse = FakeMouse()

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return "Fake page"

    def content(self):
        return "<html></html>"

    def on(self, event, handler):
        self.handlers[event] = handler

    def locator(self, selector):
        return FakeLocator(self.elements.get(selector))

    def query_selector(self, selector):
        return self.elements.get(selector)

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
    def __init__(self, url, method="GET", post_data=None):
        self.url = url
        self.method = method
        self.post_data = post_data


class FakeRoute:
    def __init__(self, url, method="GET", post_data=None):
        self.request = FakeRequest(url, method, post_data)
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
