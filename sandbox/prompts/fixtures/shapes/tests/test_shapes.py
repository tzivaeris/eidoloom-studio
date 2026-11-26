import math
import unittest

import shapes


class ShapesTests(unittest.TestCase):
    def test_functions(self):
        self.assertAlmostEqual(shapes.area_circle(2), 4 * math.pi)
        self.assertEqual(shapes.perimeter_rect(2, 3), 10)
        self.assertEqual(shapes.scale_point((1, 2), 3), (3, 6))
        self.assertEqual(shapes.bounding_box([(1, 5), (3, 2), (0, 4)]), (0, 2, 3, 5))

    def test_rectangle(self):
        r = shapes.Rectangle.from_corners(0, 0, 3, 3)
        self.assertTrue(r.is_square)
        self.assertEqual(r.area(), 9)
        self.assertEqual(shapes.describe(r), "rectangle: area 9")

    def test_circle(self):
        c = shapes.Circle.unit().scaled(2)
        self.assertEqual(c.diameter, 4)
        self.assertAlmostEqual(shapes.total_area([c, shapes.Rectangle(1, 2)]), 4 * math.pi + 2)


if __name__ == "__main__":
    unittest.main()
