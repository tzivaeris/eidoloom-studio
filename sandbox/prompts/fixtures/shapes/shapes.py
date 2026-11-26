"""Basic geometry."""
import math


def area_circle(radius):
    return math.pi * radius ** 2


def perimeter_rect(width, height):
    return 2 * (width + height)


def scale_point(point, factor=1.0):
    x, y = point
    return (x * factor, y * factor)


def bounding_box(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def describe(shape, precision=2):
    return f"{shape.name}: area {round(shape.area(), precision)}"


def total_area(shapes):
    return sum(s.area() for s in shapes)


class Rectangle:
    def __init__(self, width, height, name="rectangle"):
        self.width = width
        self.height = height
        self.name = name

    def area(self):
        return self.width * self.height

    @property
    def is_square(self):
        return self.width == self.height

    @staticmethod
    def from_corners(x1, y1, x2, y2):
        return Rectangle(abs(x2 - x1), abs(y2 - y1))


class Circle:
    def __init__(self, radius):
        self.radius = radius
        self.name = "circle"

    def area(self):
        return area_circle(self.radius)

    @property
    def diameter(self):
        return self.radius * 2

    @staticmethod
    def unit():
        return Circle(1)

    def scaled(self, factor):
        return Circle(self.radius * factor)
